"""
AI客服模块
基于 Gemini 模型的智能客服系统，支持流式传输、工具调用、文件上传和多轮对话
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path
import asyncio
import aiohttp
import json
import base64
import time
import re


class AICustomerService(commands.Cog, name="AI客服"):
    """AI客服 Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_loader = bot.config_loader
        self.conversations: dict[int, list] = {}
        self.active_channels: set[int] = set()
        self.processing_channels: set[int] = set()
        self.pending_messages: dict[int, list[discord.Message]] = {}
        self.debug_channels: set[int] = set()
        self.session: Optional[aiohttp.ClientSession] = None
        self.load_config()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

    # ── 配置管理 ──────────────────────────────────────────────

    @staticmethod
    def _load_prompt_file(path_str: str, fallback: str = '') -> str:
        """从文件路径加载提示词，文件不存在则返回 fallback"""
        if not path_str:
            return fallback
        p = Path(path_str)
        if p.exists():
            try:
                return p.read_text(encoding='utf-8').strip()
            except Exception as e:
                print(f"[AI客服] 读取提示词文件失败 {p}: {e}")
        else:
            print(f"[AI客服] 提示词文件不存在: {p}")
        return fallback

    def load_config(self):
        """加载配置"""
        cfg = self.config_loader
        self.api_key = cfg.get('ai_customer_service.gemini.api_key', '')
        self.proxy_url = cfg.get(
            'ai_customer_service.gemini.proxy_url',
            'https://generativelanguage.googleapis.com'
        ).rstrip('/')
        self.model = cfg.get('ai_customer_service.gemini.model', 'gemini-2.0-flash')
        self.system_prompt = self._load_prompt_file(
            cfg.get('ai_customer_service.gemini.system_prompt_file', ''),
            fallback='你是一个友好的AI客服助手。',
        )
        self.tail_prompt = self._load_prompt_file(
            cfg.get('ai_customer_service.gemini.tail_prompt_file', ''),
        )
        self.admin_channel_id = cfg.get('ai_customer_service.admin_channel_id', 0)
        self.auto_reply_enabled = cfg.get('ai_customer_service.auto_reply.enabled', False)
        self.auto_reply_category_ids = cfg.get('ai_customer_service.auto_reply.category_ids', [])
        self.greeting_message = cfg.get(
            'ai_customer_service.auto_reply.greeting',
            '你好！我是AI客服助手，有什么可以帮助你的吗？'
        )
        self.max_history = cfg.get('ai_customer_service.max_history', 50)
        self.max_attachment_size = cfg.get('ai_customer_service.max_attachment_size', 10 * 1024 * 1024)
        self.allowed_role_ids = cfg.get('allowed_role_ids', [])

    async def on_config_reload(self):
        """配置重载回调"""
        self.load_config()

    def _is_admin(self, member: discord.Member) -> bool:
        """判断成员是否属于管理组"""
        if member.guild_permissions.administrator:
            return True
        if not self.allowed_role_ids:
            return False
        member_role_ids = [role.id for role in member.roles]
        return any(rid in member_role_ids for rid in self.allowed_role_ids)

    # ── API 请求构建 ──────────────────────────────────────────

    def _build_system_instruction(self) -> dict:
        beijing_now = datetime.now(timezone(timedelta(hours=8)))
        time_tag = f"<time>{beijing_now.strftime('%Y-%m-%d %H:%M:%S')}</time>\n"
        prompt = time_tag + self.system_prompt
        if self.tail_prompt:
            prompt += f"\n\n---\n{self.tail_prompt}"
        return {"parts": [{"text": prompt}]}

    def _build_tools(self) -> list:
        return [{
            "functionDeclarations": [
                {
                    "name": "send_to_admin",
                    "description": "将消息发送到管理频道，用于需要人工介入或上报的情况",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "message": {
                                "type": "STRING",
                                "description": "要发送给管理员的消息内容"
                            }
                        },
                        "required": ["message"]
                    }
                },
                {
                    "name": "exit_conversation",
                    "description": "主动结束当前对话，适用于对话自然结束或用户不再需要帮助的情况",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {}
                    }
                },
                {
                    "name": "fetch_messages",
                    "description": "根据 Discord 消息链接获取该消息前后各25条聊天记录，用于查看用户提供的消息链接上下文",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "message_link": {
                                "type": "STRING",
                                "description": "Discord 消息链接，格式如 https://discord.com/channels/服务器ID/频道ID/消息ID"
                            }
                        },
                        "required": ["message_link"]
                    }
                }
            ]
        }]

    async def _download_attachment(self, url: str) -> tuple[bytes, str]:
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', 'application/octet-stream')
                    data = await resp.read()
                    return data, content_type
        except Exception:
            pass
        return b'', 'application/octet-stream'

    async def _build_user_message(self, message: discord.Message) -> dict:
        """从 Discord 消息构建 Gemini user message（含文件与图像）"""
        parts = []
        image_links: list[str] = []

        author = message.author
        user_tag = f"<odyxml:user name=\"{author.display_name}\" id=\"{author.id}\">"
        if message.content:
            parts.append({"text": f"{user_tag}\n{message.content}"})
        else:
            parts.append({"text": user_tag})

        for attachment in message.attachments:
            if attachment.size > self.max_attachment_size:
                parts.append({
                    "text": f"[文件过大已跳过: {attachment.filename} "
                            f"({attachment.size / 1024 / 1024:.1f}MB)]"
                })
                continue

            try:
                data, content_type = await self._download_attachment(attachment.url)
                if not data:
                    parts.append({"text": f"[文件下载失败: {attachment.filename}]"})
                    continue

                inline_supported = (
                    content_type.startswith('image/')
                    or content_type in (
                        'application/pdf', 'text/plain', 'text/csv',
                        'text/html', 'application/json', 'text/xml',
                    )
                )

                if inline_supported:
                    b64_data = base64.b64encode(data).decode('utf-8')
                    parts.append({
                        "inlineData": {
                            "mimeType": content_type,
                            "data": b64_data
                        }
                    })
                    if content_type.startswith('image/'):
                        image_links.append(attachment.url)
                else:
                    try:
                        text_content = data.decode('utf-8')
                        parts.append({"text": f"[文件: {attachment.filename}]\n{text_content[:5000]}"})
                    except UnicodeDecodeError:
                        parts.append({
                            "text": f"[不支持的文件类型: {attachment.filename} ({content_type})]"
                        })
            except Exception as e:
                parts.append({"text": f"[文件处理失败: {attachment.filename} - {str(e)[:100]}]"})

        if image_links:
            link_tags = "\n".join(
                f"<image_link #{i}>\n{url}\n</image_link>"
                for i, url in enumerate(image_links, 1)
            )
            parts.append({"text": link_tags})

        parts.append({"text": "</odyxml:user>"})

        return {"role": "user", "parts": parts}

    # ── Gemini API 流式调用 ───────────────────────────────────

    async def _call_gemini_stream(self, channel_id: int, bot_message: discord.Message) -> dict:
        """
        调用 Gemini 流式 API 并实时更新 bot 消息。
        返回中保留 thoughtSignature 以兼容 Gemini 2.5/3 系列模型。
        """
        history = self.conversations.get(channel_id, [])

        request_body = {
            "contents": history,
            "systemInstruction": self._build_system_instruction(),
            "tools": self._build_tools(),
        }

        url = (
            f"{self.proxy_url}/v1beta/models/{self.model}"
            f":streamGenerateContent?alt=sse&key={self.api_key}"
        )

        full_text = ""
        tool_call_parts: list[dict] = []
        text_signature: str | None = None
        last_edit_time = 0.0
        min_edit_interval = 1.0 / 3

        async with self.session.post(
            url,
            json=request_body,
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=300),
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Gemini API 错误 ({response.status}): {error_text[:500]}")

            while True:
                line_bytes = await response.content.readline()
                if not line_bytes:
                    break

                line = line_bytes.decode('utf-8').strip()
                if not line or not line.startswith('data: '):
                    continue

                json_str = line[6:]
                if json_str == '[DONE]':
                    break

                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    continue

                if 'promptFeedback' in data:
                    block_reason = data['promptFeedback'].get('blockReason', '')
                    if block_reason:
                        raise Exception(f"请求被安全策略拦截: {block_reason}")

                candidates = data.get('candidates', [])
                if not candidates:
                    continue

                candidate = candidates[0]

                finish_reason = candidate.get('finishReason', '')
                if finish_reason == 'SAFETY':
                    raise Exception("回复被安全策略拦截")

                content = candidate.get('content', {})
                parts = content.get('parts', [])

                for part in parts:
                    if 'functionCall' in part:
                        fc_part = {'functionCall': part['functionCall']}
                        if 'thoughtSignature' in part:
                            fc_part['thoughtSignature'] = part['thoughtSignature']
                        tool_call_parts.append(fc_part)
                    elif 'text' in part:
                        full_text += part['text']
                        if 'thoughtSignature' in part:
                            text_signature = part['thoughtSignature']
                        now = time.monotonic()
                        if now - last_edit_time >= min_edit_interval:
                            try:
                                display = full_text
                                if len(display) > 2000:
                                    display = display[:1997] + "..."
                                await bot_message.edit(content=display)
                                last_edit_time = now
                            except discord.HTTPException:
                                pass
                    elif 'thoughtSignature' in part:
                        text_signature = part['thoughtSignature']

        if full_text:
            try:
                display = full_text[:1997] + "..." if len(full_text) > 2000 else full_text
                await bot_message.edit(content=display)
            except discord.HTTPException:
                pass

        if tool_call_parts:
            return {
                'type': 'tool_calls',
                'tool_call_parts': tool_call_parts,
                'text': full_text,
                'text_signature': text_signature,
            }
        if full_text:
            return {'type': 'text', 'text': full_text, 'text_signature': text_signature}
        return {'type': 'empty'}

    # ── 工具执行 ──────────────────────────────────────────────

    async def _execute_tool(self, tool_call: dict, channel: discord.abc.Messageable) -> dict:
        name = tool_call.get('name', '')
        args = tool_call.get('args', {})

        if name == 'send_to_admin':
            msg_text = args.get('message', '')
            if not self.admin_channel_id:
                return {"error": "未配置管理频道"}
            admin_ch = self.bot.get_channel(self.admin_channel_id)
            if not admin_ch:
                try:
                    admin_ch = await self.bot.fetch_channel(self.admin_channel_id)
                except Exception:
                    return {"error": "无法访问管理频道"}
            await admin_ch.send(f"**来自 <#{channel.id}>：**\n{msg_text}")
            return {"result": "消息已发送到管理频道"}

        if name == 'exit_conversation':
            return {"result": "对话已结束"}

        if name == 'fetch_messages':
            link = args.get('message_link', '')
            match = re.search(r'channels/(\d+)/(\d+)/(\d+)', link)
            if not match:
                return {"error": "无效的消息链接格式"}

            ch_id, msg_id = int(match.group(2)), int(match.group(3))
            try:
                target_ch = self.bot.get_channel(ch_id)
                if not target_ch:
                    target_ch = await self.bot.fetch_channel(ch_id)
                target_msg = await target_ch.fetch_message(msg_id)
            except Exception:
                return {"error": "无法获取目标消息，可能无权限或消息不存在"}

            msgs_before = []
            async for m in target_ch.history(limit=25, before=target_msg):
                msgs_before.append(m)
            msgs_before.reverse()

            msgs_after = []
            async for m in target_ch.history(limit=25, after=target_msg):
                msgs_after.append(m)

            all_msgs = msgs_before + [target_msg] + msgs_after
            lines = []
            for m in all_msgs:
                marker = ">>>" if m.id == msg_id else "   "
                ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
                text = m.content or "(无文本)"
                atts = f" [附件: {', '.join(a.filename for a in m.attachments)}]" if m.attachments else ""
                lines.append(f"{marker} [{ts}] {m.author.display_name} ({m.author.id}): {text}{atts}")

            return {"messages": "\n".join(lines)}

        return {"error": f"未知工具: {name}"}

    # ── 响应生成（含工具调用循环） ────────────────────────────

    async def _generate_response(
        self,
        channel: discord.abc.Messageable,
        channel_id: int,
        bot_message: discord.Message,
    ):
        max_tool_rounds = 5

        for _ in range(max_tool_rounds):
            result = await self._call_gemini_stream(channel_id, bot_message)

            if result['type'] == 'text':
                text = result['text']
                text_part: dict = {"text": text}
                if result.get('text_signature'):
                    text_part['thoughtSignature'] = result['text_signature']
                self.conversations[channel_id].append({
                    "role": "model",
                    "parts": [text_part],
                })
                if len(text) > 2000:
                    for i in range(2000, len(text), 2000):
                        await channel.send(text[i:i + 2000])
                break

            if result['type'] == 'tool_calls':
                # 将模型的工具调用响应加入历史（保留 thoughtSignature）
                model_parts: list[dict] = []
                if result.get('text'):
                    model_parts.append({"text": result['text']})
                model_parts.extend(result['tool_call_parts'])
                self.conversations[channel_id].append({
                    "role": "model",
                    "parts": model_parts,
                })

                # 逐个执行工具并收集结果
                func_resp_parts = []
                exit_called = False

                for tc_part in result['tool_call_parts']:
                    fc = tc_part['functionCall']
                    tool_result = await self._execute_tool(fc, channel)
                    func_resp_parts.append({
                        "functionResponse": {
                            "name": fc['name'],
                            "response": {"content": tool_result},
                        }
                    })
                    if fc['name'] == 'exit_conversation':
                        exit_called = True

                # 将工具结果加入历史
                self.conversations[channel_id].append({
                    "role": "user",
                    "parts": func_resp_parts,
                })

                if exit_called:
                    final = result.get('text') or '对话已结束，感谢您的使用！'
                    try:
                        await bot_message.edit(content=final[:2000])
                    except discord.HTTPException:
                        pass
                    self.active_channels.discard(channel_id)
                    self.conversations.pop(channel_id, None)
                    return

                # debug 模式：显示工具调用详情
                if channel_id in self.debug_channels:
                    debug_lines = ["**[DEBUG] 工具调用**"]
                    for tc_part in result['tool_call_parts']:
                        fc = tc_part['functionCall']
                        args_str = json.dumps(fc.get('args', {}), ensure_ascii=False)
                        debug_lines.append(f"🔧 `{fc['name']}({args_str})`")
                    for fr in func_resp_parts:
                        fr_data = fr['functionResponse']
                        resp_str = json.dumps(fr_data['response'], ensure_ascii=False)
                        if len(resp_str) > 500:
                            resp_str = resp_str[:500] + "..."
                        debug_lines.append(f"📤 `{fr_data['name']}` → {resp_str}")
                    debug_text = "\n".join(debug_lines)
                    await channel.send(debug_text[:2000])

                # 已有文本时保留当前消息，新建消息用于后续回复
                if result.get('text'):
                    bot_message = await channel.send("💭 处理中...")
                else:
                    try:
                        await bot_message.edit(content="💭 处理中...")
                    except discord.HTTPException:
                        pass
                continue

            # type == 'empty'
            try:
                await bot_message.edit(content="⚠️ AI未生成回复")
            except discord.HTTPException:
                pass
            break

    # ── 消息处理入口 ──────────────────────────────────────────

    async def _append_and_respond(self, channel, channel_id: int, messages: list[discord.Message]):
        """将一批用户消息合并为一条 user turn 加入历史，然后生成回复"""
        if channel_id not in self.conversations:
            self.conversations[channel_id] = []

        # 多条消息的 parts 合并成单条 user message（Gemini 不允许连续 user turn）
        combined_parts = []
        for msg in messages:
            user_msg = await self._build_user_message(msg)
            combined_parts.extend(user_msg["parts"])

        self.conversations[channel_id].append({"role": "user", "parts": combined_parts})

        if len(self.conversations[channel_id]) > self.max_history:
            self.conversations[channel_id] = self.conversations[channel_id][-self.max_history:]

        bot_message = await channel.send("💭 思考中...")
        await self._generate_response(channel, channel_id, bot_message)

    async def _handle_message(self, message: discord.Message):
        channel = message.channel
        channel_id = channel.id

        # 频道正忙时排队，等当前回复结束后再处理
        if channel_id in self.processing_channels:
            self.pending_messages.setdefault(channel_id, []).append(message)
            return

        self.processing_channels.add(channel_id)
        try:
            await self._append_and_respond(channel, channel_id, [message])

            # 处理排队期间累积的消息
            while channel_id in self.active_channels and self.pending_messages.get(channel_id):
                queued = self.pending_messages.pop(channel_id)
                await self._append_and_respond(channel, channel_id, queued)

        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 处理消息错误: {e}"
            )
            try:
                await channel.send(f"❌ AI回复出错: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            self.processing_channels.discard(channel_id)
            self.pending_messages.pop(channel_id, None)

    # ── 事件监听 ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """检测指定分类下的新频道并启用自动回复"""
        if not self.auto_reply_enabled:
            return
        if not isinstance(channel, discord.TextChannel):
            return
        if channel.category_id not in self.auto_reply_category_ids:
            return

        self.active_channels.add(channel.id)
        self.conversations[channel.id] = []

        await asyncio.sleep(1)

        try:
            await channel.send(self.greeting_message)
            self.conversations[channel.id].append({
                "role": "model",
                "parts": [{"text": self.greeting_message}]
            })
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 新频道自动回复已启用: #{channel.name}"
            )
        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 发送问候消息失败: {e}"
            )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """频道删除时清理资源"""
        self.active_channels.discard(channel.id)
        self.conversations.pop(channel.id, None)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听活跃频道中的用户消息并自动回复"""
        if message.author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.channel.id not in self.active_channels:
            return
        if not self.api_key:
            return

        await self._handle_message(message)

    # ── 斜杠命令 ──────────────────────────────────────────────

    @app_commands.command(name="ai客服", description="在当前频道手动开启或关闭AI客服")
    @app_commands.describe(操作="开启或关闭AI客服")
    @app_commands.choices(操作=[
        app_commands.Choice(name="开启", value="on"),
        app_commands.Choice(name="关闭", value="off"),
    ])
    async def toggle_ai(self, interaction: discord.Interaction, 操作: str):
        """手动开启/关闭指定频道的AI客服"""
        channel_id = interaction.channel.id

        if 操作 == "on":
            if not self.api_key:
                await interaction.response.send_message(
                    "❌ 未配置 Gemini API Key，请在配置文件中设置",
                    ephemeral=True,
                )
                return
            self.active_channels.add(channel_id)
            if channel_id not in self.conversations:
                self.conversations[channel_id] = []
            await interaction.response.send_message("✅ AI客服已在此频道开启", ephemeral=True)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 手动开启: #{interaction.channel.name} "
                f"(操作者: {interaction.user.name})"
            )
        else:
            self.active_channels.discard(channel_id)
            self.conversations.pop(channel_id, None)
            await interaction.response.send_message("✅ AI客服已在此频道关闭", ephemeral=True)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 手动关闭: #{interaction.channel.name} "
                f"(操作者: {interaction.user.name})"
            )

    @app_commands.command(name="ai管理", description="向当前频道的AI客服发送仅管理员可见的隐藏指令")
    @app_commands.describe(消息="要发送给模型的隐藏消息（投诉人不可见）")
    async def admin_inject(self, interaction: discord.Interaction, 消息: str):
        """管理员向模型注入隐藏指令"""
        channel = interaction.channel
        channel_id = channel.id

        if not isinstance(interaction.user, discord.Member) or not self._is_admin(interaction.user):
            await interaction.response.send_message("❌ 你没有权限使用此命令", ephemeral=True)
            return

        if channel_id not in self.active_channels:
            await interaction.response.send_message("❌ 当前频道未开启AI客服", ephemeral=True)
            return

        if channel_id in self.processing_channels:
            await interaction.response.send_message("⏳ 当前频道正在处理中，请稍后重试", ephemeral=True)
            return

        self.processing_channels.add(channel_id)
        try:
            # 注入隐藏消息到对话历史
            admin_msg = {
                "role": "user",
                "parts": [{"text": f"<odyxml:admin>{消息}</odyxml:admin>"}],
            }
            if channel_id not in self.conversations:
                self.conversations[channel_id] = []
            self.conversations[channel_id].append(admin_msg)

            await interaction.response.send_message(
                f"✅ 隐藏指令已注入，正在等待模型回复...\n> {消息}",
                ephemeral=True,
            )

            # 找到最近的投诉人（非 bot、非管理组）并 @ 提醒，再编辑为思考中
            mention_user = None
            async for msg in channel.history(limit=50):
                if not msg.author.bot and isinstance(msg.author, discord.Member) and not self._is_admin(msg.author):
                    mention_user = msg.author
                    break

            if mention_user:
                bot_message = await channel.send(mention_user.mention)
                await bot_message.edit(content="💭 思考中...")
            else:
                bot_message = await channel.send("💭 思考中...")
            await self._generate_response(channel, channel_id, bot_message)

        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 管理员注入消息错误: {e}"
            )
            try:
                await channel.send(f"❌ AI回复出错: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            self.processing_channels.discard(channel_id)

    @app_commands.command(name="ai调试", description="开启或关闭当前频道的AI客服调试模式")
    @app_commands.describe(操作="开启或关闭调试模式")
    @app_commands.choices(操作=[
        app_commands.Choice(name="开启", value="on"),
        app_commands.Choice(name="关闭", value="off"),
    ])
    async def toggle_debug(self, interaction: discord.Interaction, 操作: str):
        """开关调试模式，显示完整工具调用信息"""
        if not isinstance(interaction.user, discord.Member) or not self._is_admin(interaction.user):
            await interaction.response.send_message("❌ 你没有权限使用此命令", ephemeral=True)
            return

        channel_id = interaction.channel.id
        if 操作 == "on":
            self.debug_channels.add(channel_id)
            await interaction.response.send_message("🐛 调试模式已开启", ephemeral=True)
        else:
            self.debug_channels.discard(channel_id)
            await interaction.response.send_message("🐛 调试模式已关闭", ephemeral=True)


async def setup(bot):
    """Cog 加载入口"""
    await bot.add_cog(AICustomerService(bot))
