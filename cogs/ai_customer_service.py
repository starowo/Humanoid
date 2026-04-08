"""
AI客服模块
基于 Gemini 模型的智能客服系统，支持流式传输、工具调用、文件上传和多轮对话
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Union
from pathlib import Path
import asyncio
import aiohttp
import json
import base64
import time
import re


# send_to_admin 工具发往管理区/子区的正文前缀，用于恢复持久化按钮
_ADMIN_TOOL_MESSAGE_HEADER = re.compile(r"^\*\*来自 <#(\d+)>：\*\*", re.MULTILINE)


def _admin_inject_button_custom_id(source_channel_id: int, admin_message_id: int) -> str:
    """持久化按钮 custom_id（单条管理消息唯一，便于重启后 edit + add_view）"""
    return f"humanoid_ai_inj_{source_channel_id}_{admin_message_id}"


def _admin_preset_button_custom_id(
    source_channel_id: int, admin_message_id: int, preset_index: int
) -> str:
    return f"humanoid_ai_pr_{source_channel_id}_{admin_message_id}_{preset_index}"


class AdminInjectModal(discord.ui.Modal, title="向AI发送隐藏指令"):
    """管理员快速注入指令的弹出表单"""
    指令 = discord.ui.TextInput(
        label="指令内容（投诉人不可见）",
        style=discord.TextStyle.paragraph,
        placeholder="输入要发送给AI的指令...",
        required=True,
    )

    def __init__(self, cog: "AICustomerService", source_channel_id: int):
        super().__init__()
        self.cog = cog
        self.source_channel_id = source_channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog._admin_inject_after_modal_submit(
            interaction, self.source_channel_id, self.指令.value
        )


class AdminPresetModal(discord.ui.Modal):
    """预设指令：弹出时已填入展开变量后的正文，确认即发送"""

    def __init__(self, cog: "AICustomerService", source_channel_id: int, title: str, body: str):
        super().__init__(title=title[:45])
        self.cog = cog
        self.source_channel_id = source_channel_id
        safe = body if len(body) <= 4000 else body[:3997] + "..."
        self._body = discord.ui.TextInput(
            label="内容（可修改后发送）",
            style=discord.TextStyle.paragraph,
            default=safe,
            required=True,
            max_length=4000,
        )
        self.add_item(self._body)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog._admin_inject_after_modal_submit(
            interaction, self.source_channel_id, self._body.value
        )


class AdminInjectView(discord.ui.View):
    """附在管理频道消息上的快速指令按钮（必须带 custom_id 且 timeout=None 才能持久化 / add_view）"""

    def __init__(self, cog: "AICustomerService", source_channel_id: int, admin_message_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.source_channel_id = source_channel_id
        self.admin_message_id = admin_message_id
        btn = discord.ui.Button(
            label="发送指令",
            style=discord.ButtonStyle.primary,
            emoji="📝",
            custom_id=_admin_inject_button_custom_id(source_channel_id, admin_message_id),
        )
        btn.callback = self._inject_button_callback
        self.add_item(btn)

        def bind_preset(preset_label: str, preset_body: str):
            async def _preset_callback(interaction: discord.Interaction):
                if not isinstance(interaction.user, discord.Member) or not self.cog._is_admin(
                    interaction.user
                ):
                    await interaction.response.send_message("❌ 你没有权限", ephemeral=True)
                    return
                expanded = await self.cog._expand_preset_variables(preset_body)
                await interaction.response.send_modal(
                    AdminPresetModal(self.cog, self.source_channel_id, preset_label, expanded)
                )

            return _preset_callback

        max_presets = min(len(cog.send_to_admin_preset_buttons), 24)
        for idx in range(max_presets):
            preset = cog.send_to_admin_preset_buttons[idx]
            plabel = str(preset.get("label", "预设"))[:80]
            pcontent = str(preset.get("content", ""))
            pbtn = discord.ui.Button(
                label=plabel,
                style=discord.ButtonStyle.secondary,
                custom_id=_admin_preset_button_custom_id(
                    source_channel_id, admin_message_id, idx
                ),
            )
            pbtn.callback = bind_preset(plabel, pcontent)
            self.add_item(pbtn)

    async def _inject_button_callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not self.cog._is_admin(interaction.user):
            await interaction.response.send_message("❌ 你没有权限", ephemeral=True)
            return
        await interaction.response.send_modal(
            AdminInjectModal(self.cog, self.source_channel_id)
        )


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
        self.generation_tasks: dict[int, asyncio.Task] = {}
        self.channel_complainants: dict[int, int] = {}  # channel_id -> 原始投诉人 user_id
        self.channel_threads: dict[int, discord.Thread] = {}  # channel_id -> 管理频道中的 thread
        self.session: Optional[aiohttp.ClientSession] = None
        self._openai_client: Any = None  # openai.AsyncOpenAI，惰性初始化
        self._restored_from_discord_once: bool = False
        self.load_config()

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        await self._ensure_openai_client()

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()
        await self._close_openai_client()

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
        self.llm_provider = str(
            cfg.get('ai_customer_service.provider', 'gemini')
        ).strip().lower()
        if self.llm_provider not in ('gemini', 'claude_openai'):
            self.llm_provider = 'gemini'

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

        self.claude_openai_api_key = cfg.get(
            'ai_customer_service.claude_openai.api_key', ''
        )
        _bu = cfg.get(
            'ai_customer_service.claude_openai.base_url',
            'https://api.anthropic.com/v1/',
        )
        self.claude_openai_base_url = _bu.rstrip('/') + '/'
        self.claude_openai_model = cfg.get(
            'ai_customer_service.claude_openai.model',
            'claude-sonnet-4-20250514',
        )
        self.claude_openai_max_tokens = int(
            cfg.get('ai_customer_service.claude_openai.max_tokens', 8192)
        )
        raw_thinking = cfg.get('ai_customer_service.claude_openai.thinking')
        self.claude_openai_thinking: Optional[dict[str, Any]] = (
            dict(raw_thinking) if isinstance(raw_thinking, dict) else None
        )
        self.debug_stream_full_log = bool(
            cfg.get('ai_customer_service.debug_stream_full_log', False)
        )

        self.fetch_punishments_channel_id = int(
            cfg.get(
                'ai_customer_service.send_to_admin_presets.fetch_punishments_channel_id',
                0,
            )
            or 0
        )
        raw_presets = cfg.get('ai_customer_service.send_to_admin_presets.buttons', [])
        self.send_to_admin_preset_buttons: list[dict] = []
        if isinstance(raw_presets, list):
            for p in raw_presets:
                if (
                    isinstance(p, dict)
                    and p.get('label') is not None
                    and p.get('content') is not None
                ):
                    self.send_to_admin_preset_buttons.append({
                        'label': str(p['label']),
                        'content': str(p['content']),
                    })

    async def on_config_reload(self):
        """配置重载回调"""
        self.load_config()
        await self._ensure_openai_client()

    def _llm_configured(self) -> bool:
        if self.llm_provider == 'claude_openai':
            return bool(self.claude_openai_api_key)
        return bool(self.api_key)

    async def _close_openai_client(self):
        if self._openai_client is not None:
            try:
                await self._openai_client.close()
            except Exception:
                pass
            self._openai_client = None

    async def _ensure_openai_client(self):
        """按当前 provider 创建或关闭 AsyncOpenAI 客户端"""
        await self._close_openai_client()
        if self.llm_provider != 'claude_openai' or not self.claude_openai_api_key:
            return
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 未安装 openai 包，无法使用 claude_openai: {e}"
            )
            return
        self._openai_client = AsyncOpenAI(
            api_key=self.claude_openai_api_key,
            base_url=self.claude_openai_base_url,
        )

    def _claude_openai_extra_body(self) -> Optional[dict[str, Any]]:
        """
        Anthropic OpenAI 兼容层 extended thinking：
        extra_body['thinking'] = {'type': 'enabled', 'budget_tokens': n}
        配置见 ai_customer_service.claude_openai.thinking（enabled / effort / budget_tokens）。
        """
        t = self.claude_openai_thinking
        if not t:
            return None
        if not t.get('enabled'):
            return None
        budget = t.get('budget_tokens')
        if budget is None:
            effort = str(t.get('effort', 'medium')).lower()
            budget = {
                'low': 2048,
                'medium': 8000,
                'high': 16000,
            }.get(effort, 8000)
        try:
            budget_i = int(budget)
        except (TypeError, ValueError):
            budget_i = 8000
        if budget_i < 1024:
            budget_i = 1024
        return {'thinking': {'type': 'enabled', 'budget_tokens': budget_i}}

    def _debug_log_after_stream(
        self,
        backend: str,
        channel_id: int,
        payload: dict[str, Any],
    ) -> None:
        """流式结束后全量打印模型侧信息（思考、工具、用量等），需开启 debug_stream_full_log"""
        if not self.debug_stream_full_log:
            return
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        except Exception as e:
            print(f"[{ts}] [AI客服][stream_debug] JSON 序列化失败: {e}")
            return
        max_len = 200_000
        if len(text) > max_len:
            text = text[:max_len] + f'\n... [共截断 {len(text)} 字符]'
        print(f"[{ts}] [AI客服][stream_debug] backend={backend} channel_id={channel_id}\n{text}")

    async def _admin_inject_after_modal_submit(
        self,
        interaction: discord.Interaction,
        channel_id: int,
        msg_text: str,
    ):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("❌ 无法找到来源频道", ephemeral=True)
            return
        if channel_id not in self.active_channels:
            await interaction.response.send_message("❌ 该频道AI客服已关闭", ephemeral=True)
            return
        if channel_id in self.processing_channels:
            await interaction.response.send_message(
                "⏳ 该频道正在处理中，请稍后重试",
                ephemeral=True,
            )
            return

        self.processing_channels.add(channel_id)
        try:
            if channel_id not in self.conversations:
                self.conversations[channel_id] = []
            self.conversations[channel_id].append({
                "role": "user",
                "parts": [{"text": f"<odyxml:admin>{msg_text}</odyxml:admin>"}],
            })

            await interaction.response.send_message(
                f"✅ 已向 <#{channel_id}> 注入指令\n> {msg_text}",
                ephemeral=True,
            )

            mention_user = None
            async for msg in channel.history(limit=50):
                au = msg.author
                if (
                    not msg.author.bot
                    and isinstance(au, discord.Member)
                    and not self._is_admin(au)
                ):
                    mention_user = au
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
                f"[AI客服] 表单注入错误: {e}"
            )
            try:
                await channel.send(f"❌ AI回复出错: {str(e)[:200]}")
            except Exception:
                pass
        finally:
            self.processing_channels.discard(channel_id)

    @staticmethod
    def _embed_to_plain_text(embed: discord.Embed) -> str:
        lines: list[str] = []
        if embed.title:
            lines.append(embed.title)
        if embed.description:
            lines.append(embed.description)
        if embed.author and embed.author.name:
            lines.append(embed.author.name)
        for field in embed.fields:
            if field.name:
                lines.append(field.name)
            if field.value:
                lines.append(field.value)
        if embed.footer and embed.footer.text:
            lines.append(embed.footer.text)
        return "\n".join(lines).strip()

    async def _format_user_mentions_in_text(
        self,
        text: str,
        guild: Optional[discord.Guild],
    ) -> str:
        """将 <@id> 转为 用户昵称（用户名）（数字id）"""
        if not text:
            return text
        pattern = re.compile(r"<@!?(\d+)>")

        async def uid_to_label(uid: int) -> str:
            if guild is not None:
                member = guild.get_member(uid)
                if member is None:
                    try:
                        member = await guild.fetch_member(uid)
                    except discord.NotFound:
                        member = None
                if member is not None:
                    return f"{member.display_name}（{member.name}）（{member.id}）"
            try:
                user = await self.bot.fetch_user(uid)
                return f"{user.display_name}（{user.name}）（{user.id}）"
            except Exception:
                return f"用户（?）（{uid}）"

        chunks: list[str] = []
        pos = 0
        for m in pattern.finditer(text):
            chunks.append(text[pos : m.start()])
            chunks.append(await uid_to_label(int(m.group(1))))
            pos = m.end()
        chunks.append(text[pos:])
        return "".join(chunks)

    async def _expand_fetch_punishments(self) -> str:
        """%fetch_punishments%：在最近 10 条消息中取含 embed 的条目并格式化"""
        cid = self.fetch_punishments_channel_id
        if not cid:
            return "（未配置 send_to_admin_presets.fetch_punishments_channel_id）"
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception:
                return "（无法访问处罚记录频道）"
        if not isinstance(ch, discord.TextChannel):
            return "（处罚记录频道不是文字频道）"
        guild = ch.guild
        embed_msgs: list[discord.Message] = []
        async for msg in ch.history(limit=10):
            if msg.embeds:
                embed_msgs.append(msg)
        embed_msgs.reverse()
        blocks: list[str] = []
        for msg in embed_msgs:
            parts: list[str] = []
            for emb in msg.embeds:
                plain = self._embed_to_plain_text(emb)
                if plain:
                    parts.append(plain)
            body = "\n".join(parts).strip()
            if not body:
                body = "（空 embed）"
            body = await self._format_user_mentions_in_text(body, guild)
            blocks.append(f"消息链接：{msg.jump_url}\n处罚内容：{body}")
        if not blocks:
            return "（近 10 条消息中无 embed）"
        return "\n\n".join(blocks)

    async def _expand_preset_variables(self, template: str) -> str:
        out = template
        if "%fetch_punishments%" in out:
            repl = await self._expand_fetch_punishments()
            out = out.replace("%fetch_punishments%", repl)
        return out

    def _is_transient_bot_chunk(self, message: discord.Message) -> bool:
        """重建历史时忽略本 Bot 的中间状态占位消息"""
        if message.author.id != self.bot.user.id:
            return False
        text = (message.content or "").strip()
        if not text:
            return True
        if text in ("💭 思考中...", "💭 处理中..."):
            return True
        if text.startswith("⏳"):
            return True
        if text.startswith("⚠️ AI未生成回复"):
            return True
        return False

    async def _find_admin_thread_by_channel_name(
        self,
        admin_parent: discord.abc.GuildChannel,
        channel_name: str,
    ) -> Optional[discord.Thread]:
        """在管理频道（文字帖/论坛）下按子区名称匹配，先活跃再归档；搜不到返回 None"""
        if not isinstance(admin_parent, (discord.TextChannel, discord.ForumChannel)):
            return None
        try:
            for t in admin_parent.threads:
                if t.name == channel_name:
                    return t
            async for t in admin_parent.archived_threads(limit=100):
                if t.name == channel_name:
                    return t
        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 搜索管理子区失败 ({channel_name}): {e}",
            )
        return None

    def _message_has_send_inject_button(self, message: discord.Message) -> bool:
        """是否为附带「发送指令」按钮的管理同步消息（discord.components 结构）"""
        if not message.components:
            return False
        for row in message.components:
            for comp in row.children:
                if comp.type != discord.ComponentType.button:
                    continue
                if getattr(comp, "label", None) == "发送指令":
                    return True
        return False

    async def _reregister_admin_inject_views_in_thread(
        self,
        thread: discord.Thread,
        expected_source_channel_id: int,
        history_limit: int = 200,
    ) -> int:
        """
        在已匹配的管理子区内扫描助手历史消息：edit 附上带 custom_id 的新 View，再 add_view。
        只处理正文中来源频道 ID 与 expected_source_channel_id 一致的消息。
        """
        n = 0
        async for msg in thread.history(limit=history_limit):
            if msg.author.id != self.bot.user.id:
                continue
            if not self._message_has_send_inject_button(msg):
                continue
            m = _ADMIN_TOOL_MESSAGE_HEADER.search(msg.content or "")
            if not m:
                continue
            src_id = int(m.group(1))
            if src_id != expected_source_channel_id:
                continue
            try:
                view = AdminInjectView(self, src_id, msg.id)
                await msg.edit(content=msg.content, view=view)
                self.bot.add_view(view, message_id=msg.id)
                n += 1
            except Exception as e:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"[AI客服] 重新绑定单条管理消息 {msg.id} 失败: {e}",
                )
        return n

    async def _rebuild_conversation_from_history(
        self,
        channel: discord.TextChannel,
    ) -> tuple[list, Optional[int]]:
        """
        从频道消息历史重建 Gemini contents（由旧到新）。
        返回 (对话列表, 投诉人 user_id 若可从历史中确定否则 None)。
        """
        complainant_id: Optional[int] = None
        conv: list = []
        bot_uid = self.bot.user.id
        # 多取一些消息：一轮对话可能含多条 Bot 分包
        msg_limit = max(150, self.max_history * 4)
        pending_bot_chunks: list[str] = []

        def flush_bot():
            nonlocal pending_bot_chunks
            if not pending_bot_chunks:
                return
            merged = "\n".join(pending_bot_chunks).strip()
            pending_bot_chunks = []
            if merged:
                conv.append({"role": "model", "parts": [{"text": merged}]})

        async for msg in channel.history(limit=msg_limit, oldest_first=True):
            if msg.type not in (discord.MessageType.default, discord.MessageType.reply):
                continue
            if msg.author.bot:
                if msg.author.id == bot_uid:
                    if self._is_transient_bot_chunk(msg):
                        continue
                    text = (msg.content or "").strip()
                    if text:
                        pending_bot_chunks.append(msg.content or "")
                else:
                    flush_bot()
                    conv.append(await self._build_user_message(msg))
                continue

            flush_bot()
            if complainant_id is None:
                m = msg.author
                if isinstance(m, discord.Member) and self._is_admin(m):
                    pass
                else:
                    complainant_id = msg.author.id
            conv.append(await self._build_user_message(msg))

        flush_bot()

        if len(conv) > self.max_history:
            conv = conv[-self.max_history :]

        return conv, complainant_id

    async def _restore_auto_reply_state(self):
        """启动时从 Discord 拉回分类下各频道消息与管理子区，修复内存中的对话与映射"""
        if not self.auto_reply_enabled or not self.auto_reply_category_ids:
            return
        if not self._llm_configured():
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                "[AI客服] 跳过对话恢复：未配置当前 provider 所需的 API Key",
            )
            return
        if not self.session or self.session.closed:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                "[AI客服] 跳过对话恢复：HTTP 会话未就绪",
            )
            return

        admin_parent: Optional[discord.abc.GuildChannel] = None
        if self.admin_channel_id:
            admin_parent = self.bot.get_channel(self.admin_channel_id)
            if not admin_parent:
                try:
                    admin_parent = await self.bot.fetch_channel(self.admin_channel_id)
                except Exception:
                    admin_parent = None

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            "[AI客服] 开始从 Discord 恢复投诉频道状态…",
        )

        restored = 0
        for guild in self.bot.guilds:
            for cat_id in self.auto_reply_category_ids:
                category = guild.get_channel(cat_id)
                if category is None or not isinstance(category, discord.CategoryChannel):
                    continue
                for ch in category.text_channels:
                    try:
                        conv, complainant_id = await self._rebuild_conversation_from_history(ch)
                        self.conversations[ch.id] = conv
                        self.active_channels.add(ch.id)
                        if complainant_id is not None:
                            self.channel_complainants[ch.id] = complainant_id

                        mapped: Optional[discord.Thread] = None
                        rebound = 0
                        if admin_parent:
                            mapped = await self._find_admin_thread_by_channel_name(
                                admin_parent, ch.name
                            )
                            if mapped:
                                self.channel_threads[ch.id] = mapped
                                rebound = await self._reregister_admin_inject_views_in_thread(
                                    mapped, ch.id
                                )

                        restored += 1
                        if mapped and rebound:
                            tip = f"+管理子区 #{mapped.name}，已恢复 {rebound} 条「发送指令」按钮"
                        elif mapped:
                            tip = f"+管理子区 #{mapped.name}"
                        else:
                            tip = "（未匹配管理子区）"
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"[AI客服] 已恢复 #{ch.name}（{len(conv)} 轮对话）{tip}",
                        )
                    except Exception as e:
                        print(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"[AI客服] 恢复频道 #{getattr(ch, 'name', ch.id)} 失败: {e}",
                        )

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"[AI客服] 投诉频道状态恢复结束，共处理 {restored} 个文字频道",
        )

    async def _complaint_channel_still_exists(self, channel_id: int) -> bool:
        """投诉文字频道是否仍存在（get_channel 之外再 fetch，减轻缓存未命中误杀）"""
        if self.bot.get_channel(channel_id) is not None:
            return True
        try:
            await self.bot.fetch_channel(channel_id)
            return True
        except discord.NotFound:
            return False
        except Exception:
            return True

    async def _parse_complaint_channel_id_from_admin_thread(
        self, thread: discord.Thread,
    ) -> Optional[int]:
        """从子区内本 Bot 早期消息解析投诉频道 ID（新投诉通知 / send_to_admin 头）"""
        header_pat = re.compile(r"\*\*来自 <#(\d+)>")
        notify_pat = re.compile(r"新投诉频道 <#(\d+)>")
        async for msg in thread.history(limit=50, oldest_first=True):
            if msg.author.id != self.bot.user.id:
                continue
            text = msg.content or ""
            m = notify_pat.search(text)
            if m:
                return int(m.group(1))
            m = header_pat.search(text)
            if m:
                return int(m.group(1))
        return None

    def _monitored_complaint_channel_names(self, guild: discord.Guild) -> set[str]:
        """配置的分类下现存投诉文字频道的名称集合"""
        names: set[str] = set()
        for cat_id in self.auto_reply_category_ids:
            cat = guild.get_channel(cat_id)
            if isinstance(cat, discord.CategoryChannel):
                for ch in cat.text_channels:
                    names.add(ch.name)
        return names

    async def _maybe_archive_orphan_admin_thread(
        self,
        thread: discord.Thread,
        guild: discord.Guild,
    ) -> bool:
        """
        若子区无法对应仍存在的投诉频道则归档。
        优先根据子区历史解析频道 ID；无法解析时在监控分类中按子区名匹配。
        """
        if thread.archived:
            return False

        cid = await self._parse_complaint_channel_id_from_admin_thread(thread)
        should_archive = False
        reason = ""

        if cid is not None:
            if not await self._complaint_channel_still_exists(cid):
                should_archive = True
                reason = f"映射投诉频道 {cid} 已不存在"
        elif self.auto_reply_category_ids:
            names = self._monitored_complaint_channel_names(guild)
            if thread.name not in names:
                should_archive = True
                reason = (
                    f"子区「{thread.name}」在监控分类中无同名文字频道"
                )

        if not should_archive:
            return False
        try:
            await thread.edit(archived=True)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 已归档无主投诉频道子区 #{thread.name} ({thread.id})：{reason}",
            )
            stale_keys = [k for k, t in self.channel_threads.items() if t.id == thread.id]
            for k in stale_keys:
                self.channel_threads.pop(k, None)
            return True
        except discord.Forbidden:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 无权归档子区 {thread.id}",
            )
        except discord.HTTPException as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 归档子区 {thread.id} 失败: {e}",
            )
        return False

    async def _archive_orphan_admin_threads_at_startup(self):
        """启动时扫描管理频道下的活动子区，无对应投诉频道则 archived=True"""
        if not self.admin_channel_id:
            return
        admin_parent = self.bot.get_channel(self.admin_channel_id)
        if admin_parent is None:
            try:
                admin_parent = await self.bot.fetch_channel(self.admin_channel_id)
            except Exception:
                return
        if not isinstance(admin_parent, (discord.TextChannel, discord.ForumChannel)):
            return
        guild = admin_parent.guild
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            "[AI客服] 扫描管理频道子区，清理无主映射…",
        )
        n = 0
        for t in list(admin_parent.threads):
            try:
                if await self._maybe_archive_orphan_admin_thread(t, guild):
                    n += 1
            except Exception as e:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"[AI客服] 检查子区 {getattr(t, 'id', '?')} 时出错: {e}",
                )
        if n:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 无主投诉子区归档完成，共 {n} 个",
            )

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
                },
                {
                    "name": "no_response",
                    "description": "决定不回复当前消息，静默处理。适用于无需AI回应的场景（如用户闲聊、无关内容等）",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {}
                    }
                }
            ]
        }]

    def _system_prompt_text(self) -> str:
        parts = self._build_system_instruction().get('parts', [])
        return ''.join(
            p.get('text', '')
            for p in parts
            if isinstance(p, dict) and 'text' in p
        )

    def _gemini_value_to_json_schema(self, node: dict) -> dict[str, Any]:
        """将 Gemini functionDeclaration.parameters 转为 JSON Schema（OpenAI tools）。"""
        if not node:
            return {'type': 'object', 'properties': {}}
        raw_t = str(node.get('type', 'OBJECT')).upper()
        if raw_t == 'OBJECT':
            out: dict[str, Any] = {'type': 'object'}
            props = node.get('properties') or {}
            if props:
                out['properties'] = {
                    k: self._gemini_value_to_json_schema(v)
                    for k, v in props.items()
                }
            req = node.get('required')
            if req:
                out['required'] = list(req)
            return out
        if raw_t == 'ARRAY':
            items = node.get('items')
            return {
                'type': 'array',
                'items': self._gemini_value_to_json_schema(items)
                if items
                else {'type': 'string'},
            }
        scalar = {
            'STRING': 'string',
            'INTEGER': 'integer',
            'NUMBER': 'number',
            'BOOLEAN': 'boolean',
        }.get(raw_t, 'string')
        out: dict[str, Any] = {'type': scalar}
        if node.get('description'):
            out['description'] = node['description']
        if 'enum' in node:
            out['enum'] = list(node['enum'])
        return out

    def _build_openai_tools(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for decl in self._build_tools()[0].get('functionDeclarations', []):
            params = decl.get('parameters') or {
                'type': 'OBJECT',
                'properties': {},
            }
            out.append({
                'type': 'function',
                'function': {
                    'name': decl['name'],
                    'description': decl.get('description', ''),
                    'parameters': self._gemini_value_to_json_schema(params),
                },
            })
        return out

    def _gemini_user_parts_to_openai_content(
        self, parts: list,
    ) -> Union[str, list[dict[str, Any]]]:
        chunks: list[dict[str, Any]] = []
        for p in parts:
            if not isinstance(p, dict):
                continue
            if 'text' in p:
                chunks.append({'type': 'text', 'text': p['text']})
            elif 'inlineData' in p:
                mime = p['inlineData'].get('mimeType', 'application/octet-stream')
                b64 = p['inlineData'].get('data', '')
                if mime.startswith('image/'):
                    chunks.append({
                        'type': 'image_url',
                        'image_url': {'url': f'data:{mime};base64,{b64}'},
                    })
                else:
                    chunks.append({
                        'type': 'text',
                        'text': f'[非内联图片附件: {mime}]',
                    })
        if not chunks:
            return ''
        if len(chunks) == 1 and chunks[0]['type'] == 'text':
            return chunks[0]['text']
        return chunks

    def _gemini_contents_to_openai_messages(self, contents: list) -> list[dict[str, Any]]:
        """将内存中的 Gemini contents 转为 OpenAI Chat Completions messages（含 system）。"""
        messages: list[dict[str, Any]] = []
        sys_t = self._system_prompt_text()
        if sys_t:
            messages.append({'role': 'system', 'content': sys_t})

        pending_tool_ids: list[str] = []
        call_seq = 0

        for bi, block in enumerate(contents):
            if not isinstance(block, dict):
                continue
            role = block.get('role')
            parts = block.get('parts') or []

            if role == 'user':
                if parts and all(
                    isinstance(p, dict) and 'functionResponse' in p for p in parts
                ):
                    for j, p in enumerate(parts):
                        fr = p['functionResponse']
                        inner = fr.get('response', fr)
                        payload = (
                            inner
                            if isinstance(inner, str)
                            else json.dumps(inner, ensure_ascii=False)
                        )
                        tid = (
                            pending_tool_ids[j]
                            if j < len(pending_tool_ids)
                            else f'legacy_{bi}_{j}'
                        )
                        messages.append({
                            'role': 'tool',
                            'tool_call_id': tid,
                            'content': payload,
                        })
                    pending_tool_ids = []
                    continue
                messages.append({
                    'role': 'user',
                    'content': self._gemini_user_parts_to_openai_content(parts),
                })
            elif role == 'model':
                text_buf: list[str] = []
                tool_calls_oai: list[dict[str, Any]] = []
                pending_tool_ids = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    if 'text' in p:
                        text_buf.append(p['text'])
                    elif 'functionCall' in p:
                        fc = p['functionCall']
                        call_seq += 1
                        tid = f'call_{bi}_{call_seq}'
                        pending_tool_ids.append(tid)
                        args = fc.get('args', {})
                        tool_calls_oai.append({
                            'id': tid,
                            'type': 'function',
                            'function': {
                                'name': fc['name'],
                                'arguments': json.dumps(args, ensure_ascii=False)
                                if args
                                else '{}',
                            },
                        })
                asst: dict[str, Any] = {'role': 'assistant'}
                if text_buf:
                    asst['content'] = ''.join(text_buf)
                else:
                    asst['content'] = None
                if tool_calls_oai:
                    asst['tool_calls'] = tool_calls_oai
                messages.append(asst)

        return messages

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
        is_admin = isinstance(author, discord.Member) and self._is_admin(author)
        role_attr = ' role="admin"' if is_admin else ''
        user_tag = f"<odyxml:user name=\"{author.display_name}\" id=\"{author.id}\"{role_attr}>"
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
            "generationConfig": {
                "thinkingConfig": {
                    "includeThoughts": True,
                },
            },
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
        max_retries = 3
        debug_gemini_thoughts: list[Any] = []
        debug_gemini_usage: Any = None
        debug_gemini_last_meta: dict[str, Any] = {}

        for attempt in range(max_retries):
            resp = await self.session.post(
                url,
                json=request_body,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=300),
            )
            if resp.status in (429, 524):
                resp.release()
                retry_after = int(resp.headers.get('Retry-After', 10))
                if attempt < max_retries - 1:
                    try:
                        await bot_message.edit(content=f"⏳ API 限流/过载，{retry_after}秒后重试...")
                    except discord.HTTPException:
                        pass
                    await asyncio.sleep(retry_after)
                    continue
            break

        async with resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise Exception(f"Gemini API 错误 ({resp.status}): {error_text[:500]}")

            while True:
                line_bytes = await resp.content.readline()
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

                if self.debug_stream_full_log:
                    debug_gemini_last_meta = {
                        'finishReason': candidate.get('finishReason'),
                        'safetyRatings': candidate.get('safetyRatings'),
                    }

                if 'usageMetadata' in data:
                    if self.debug_stream_full_log:
                        debug_gemini_usage = data['usageMetadata']

                content = candidate.get('content', {})
                parts = content.get('parts', [])

                for part in parts:
                    if part.get('thought'):
                        if self.debug_stream_full_log:
                            debug_gemini_thoughts.append(part)
                        continue
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
            result: dict[str, Any] = {
                'type': 'tool_calls',
                'tool_call_parts': tool_call_parts,
                'text': full_text,
                'text_signature': text_signature,
            }
        elif full_text:
            result = {
                'type': 'text',
                'text': full_text,
                'text_signature': text_signature,
            }
        else:
            result = {'type': 'empty'}

        if self.debug_stream_full_log:
            self._debug_log_after_stream('gemini', channel_id, {
                'gemini_model': self.model,
                'proxy_url': self.proxy_url,
                'response': result,
                'assistant_visible_text': full_text,
                'tool_call_parts': tool_call_parts,
                'thought_parts_raw': debug_gemini_thoughts,
                'usageMetadata': debug_gemini_usage,
                'last_candidate_meta': debug_gemini_last_meta,
                'text_signature': text_signature,
            })
        return result

    async def _call_claude_openai_stream(
        self,
        channel_id: int,
        bot_message: discord.Message,
    ) -> dict:
        """
        经 Anthropic [OpenAI SDK 兼容层](https://platform.claude.com/docs/en/api/openai-sdk)
        调用 Claude（chat.completions + tools），流式更新 bot_message。
        对话仍存为 Gemini 形态，请求前转换为 OpenAI messages。
        """
        if self._openai_client is None:
            raise RuntimeError(
                'OpenAI 兼容客户端未就绪：请设置 provider 为 claude_openai 并配置 API Key，'
                '且已 pip 安装 openai'
            )

        history = self.conversations.get(channel_id, [])
        messages = self._gemini_contents_to_openai_messages(history)
        tools = self._build_openai_tools()

        last_edit_time = 0.0
        min_edit_interval = 1.0 / 3
        max_retries = 3

        stream_chunk_count = 0
        stream_reasoning_parts: list[str] = []
        stream_debug_chunks: list[Any] = []
        claude_usage_final: Any = None

        for attempt in range(max_retries):
            full_text = ''
            tool_acc = {}
            finish_reason = None
            stream_chunk_count = 0
            stream_reasoning_parts = []
            stream_debug_chunks = []
            claude_usage_final = None
            try:
                _ckwargs: dict[str, Any] = {
                    'model': self.claude_openai_model,
                    'messages': messages,
                    'tools': tools,
                    'max_tokens': self.claude_openai_max_tokens,
                    'stream': True,
                    'parallel_tool_calls': True,
                }
                _extra = self._claude_openai_extra_body()
                if _extra:
                    _ckwargs['extra_body'] = _extra
                stream = await self._openai_client.chat.completions.create(
                    **_ckwargs
                )
                async for chunk in stream:
                    stream_chunk_count += 1
                    if self.debug_stream_full_log:
                        u_obj = getattr(chunk, 'usage', None)
                        if u_obj is not None:
                            try:
                                claude_usage_final = (
                                    u_obj.model_dump(exclude_none=True)
                                    if hasattr(u_obj, 'model_dump')
                                    else u_obj
                                )
                            except Exception:
                                claude_usage_final = u_obj
                        if hasattr(chunk, 'model_dump'):
                            try:
                                stream_debug_chunks.append(
                                    chunk.model_dump(exclude_none=True)
                                )
                                if len(stream_debug_chunks) > 2000:
                                    stream_debug_chunks = stream_debug_chunks[
                                        -2000:
                                    ]
                            except Exception:
                                pass
                    if not chunk.choices:
                        continue
                    ch0 = chunk.choices[0]
                    if ch0.finish_reason:
                        finish_reason = ch0.finish_reason
                    delta = ch0.delta
                    if delta is not None and self.debug_stream_full_log:
                        for attr in (
                            'reasoning_content',
                            'reasoning',
                            'thinking',
                            'refusal',
                        ):
                            try:
                                v = getattr(delta, attr, None)
                            except Exception:
                                v = None
                            if v:
                                stream_reasoning_parts.append(f'[{attr}]{v!s}')
                    if getattr(delta, 'content', None):
                        full_text += delta.content or ''
                        now = time.monotonic()
                        if now - last_edit_time >= min_edit_interval:
                            try:
                                display = (
                                    full_text[:1997] + '...'
                                    if len(full_text) > 2000
                                    else full_text
                                )
                                await bot_message.edit(
                                    content=display if display else '…'
                                )
                                last_edit_time = now
                            except discord.HTTPException:
                                pass
                    tcd = getattr(delta, 'tool_calls', None)
                    if tcd:
                        for tc in tcd:
                            idx = tc.index
                            if idx not in tool_acc:
                                tool_acc[idx] = {
                                    'id': '',
                                    'name': '',
                                    'arguments': '',
                                }
                            if tc.id:
                                tool_acc[idx]['id'] = tc.id
                            fn = tc.function
                            if fn:
                                if fn.name:
                                    tool_acc[idx]['name'] = fn.name
                                if fn.arguments:
                                    tool_acc[idx]['arguments'] += fn.arguments

                break
            except Exception as e:
                msg = str(e).lower()
                if attempt < max_retries - 1 and (
                    '429' in msg or 'rate' in msg or 'overloaded' in msg
                ):
                    try:
                        await bot_message.edit(
                            content='⏳ Claude API 限流，数秒后重试...'
                        )
                    except discord.HTTPException:
                        pass
                    await asyncio.sleep(8)
                    continue
                raise

        if full_text:
            try:
                disp = (
                    full_text[:1997] + '...'
                    if len(full_text) > 2000
                    else full_text
                )
                await bot_message.edit(content=disp)
            except discord.HTTPException:
                pass

        has_tools = bool(tool_acc) or finish_reason == 'tool_calls'
        result_claude: dict[str, Any]
        if has_tools and tool_acc:
            oai_tool_parts: list[dict] = []
            for idx in sorted(tool_acc.keys()):
                acc = tool_acc[idx]
                name = acc.get('name', '')
                if not name:
                    continue
                raw_args = acc.get('arguments') or '{}'
                try:
                    args_parsed = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    args_parsed = {}
                oai_tool_parts.append({
                    'functionCall': {
                        'name': name,
                        'args': args_parsed
                        if isinstance(args_parsed, dict)
                        else {},
                    },
                })
            if oai_tool_parts:
                result_claude = {
                    'type': 'tool_calls',
                    'tool_call_parts': oai_tool_parts,
                    'text': full_text,
                    'text_signature': None,
                }
            elif full_text:
                result_claude = {
                    'type': 'text',
                    'text': full_text,
                    'text_signature': None,
                }
            else:
                result_claude = {'type': 'empty'}
        elif full_text:
            result_claude = {
                'type': 'text',
                'text': full_text,
                'text_signature': None,
            }
        else:
            result_claude = {'type': 'empty'}

        if self.debug_stream_full_log:
            self._debug_log_after_stream('claude_openai', channel_id, {
                'model': self.claude_openai_model,
                'extra_body': self._claude_openai_extra_body(),
                'finish_reason': finish_reason,
                'stream_chunk_count': stream_chunk_count,
                'reasoning_delta_joined': '\n'.join(stream_reasoning_parts),
                'tool_calls_merged_raw': dict(tool_acc),
                'response': result_claude,
                'assistant_visible_text': full_text,
                'sse_chunk_events': stream_debug_chunks,
                'usage_final': claude_usage_final,
            })
        return result_claude

    async def _call_llm_stream(
        self,
        channel_id: int,
        bot_message: discord.Message,
    ) -> dict:
        if self.llm_provider == 'claude_openai':
            return await self._call_claude_openai_stream(channel_id, bot_message)
        return await self._call_gemini_stream(channel_id, bot_message)

    # ── 工具执行 ──────────────────────────────────────────────

    async def _execute_tool(self, tool_call: dict, channel: discord.abc.Messageable) -> dict:
        name = tool_call.get('name', '')
        args = tool_call.get('args', {})

        if name == 'send_to_admin':
            msg_text = args.get('message', '')
            if not self.admin_channel_id:
                return {"error": "未配置管理频道"}
            target = self.channel_threads.get(channel.id)
            if not target:
                target = self.bot.get_channel(self.admin_channel_id)
                if not target:
                    try:
                        target = await self.bot.fetch_channel(self.admin_channel_id)
                    except Exception:
                        return {"error": "无法访问管理频道"}
            content = f"**来自 <#{channel.id}>：**\n{msg_text}"
            sent = await target.send(content)
            view = AdminInjectView(self, channel.id, sent.id)
            await sent.edit(view=view)
            self.bot.add_view(view, message_id=sent.id)
            return {"result": "消息已发送到管理频道"}

        if name == 'exit_conversation':
            return {"result": "对话已结束"}

        if name == 'no_response':
            return {"result": "已静默处理"}

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
            result = await self._call_llm_stream(channel_id, bot_message)

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
                no_response_called = False

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
                    elif fc['name'] == 'no_response':
                        no_response_called = True

                # 将工具结果加入历史
                self.conversations[channel_id].append({
                    "role": "user",
                    "parts": func_resp_parts,
                })

                if no_response_called:
                    try:
                        await bot_message.delete()
                    except discord.HTTPException:
                        pass
                    return

                if exit_called:
                    final = result.get('text') or '对话已结束，感谢您的使用！'
                    try:
                        await bot_message.edit(content=final[:2000])
                    except discord.HTTPException:
                        pass
                    self.active_channels.discard(channel_id)
                    self.conversations.pop(channel_id, None)
                    self.channel_complainants.pop(channel_id, None)
                    self.channel_threads.pop(channel_id, None)
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

    async def _run_generation(self, channel, channel_id: int, messages: list[discord.Message]):
        """构建 user turn、发起可取消的生成任务"""
        if channel_id not in self.conversations:
            self.conversations[channel_id] = []

        combined_parts = []
        for msg in messages:
            user_msg = await self._build_user_message(msg)
            combined_parts.extend(user_msg["parts"])

        # 记录插入点，取消时据此回滚
        rollback_index = len(self.conversations[channel_id])
        self.conversations[channel_id].append({"role": "user", "parts": combined_parts})

        conv = self.conversations[channel_id]
        if len(conv) > self.max_history:
            overflow = len(conv) - self.max_history
            self.conversations[channel_id] = conv[-self.max_history:]
            rollback_index = max(0, rollback_index - overflow)

        bot_message = await channel.send("💭 思考中...")

        gen_task = asyncio.create_task(
            self._generate_response(channel, channel_id, bot_message)
        )
        self.generation_tasks[channel_id] = gen_task

        try:
            await gen_task
        except asyncio.CancelledError:
            # 回滚优先（同步操作，保证执行）：移除本轮 user turn 及所有不完整的 model/function 响应
            convos = self.conversations.get(channel_id, [])
            if len(convos) > rollback_index:
                del convos[rollback_index:]
            # 再尝试删除未完成的 bot 消息
            try:
                await bot_message.delete()
            except Exception:
                pass

    async def _record_only(self, channel_id: int, message: discord.Message):
        """仅将消息记入对话历史，不触发 AI 回复"""
        if channel_id not in self.conversations:
            self.conversations[channel_id] = []
        user_msg = await self._build_user_message(message)
        self.conversations[channel_id].append(user_msg)
        if len(self.conversations[channel_id]) > self.max_history:
            self.conversations[channel_id] = self.conversations[channel_id][-self.max_history:]

    def _is_complainant(self, channel_id: int, user_id: int) -> bool:
        """判断是否为该频道的原始投诉人"""
        if channel_id not in self.channel_complainants:
            self.channel_complainants[channel_id] = user_id
            return True
        return self.channel_complainants[channel_id] == user_id

    def _should_respond(self, channel_id: int, message: discord.Message) -> bool:
        """判断是否应对该消息触发 AI 回复（仅原始投诉人）"""
        is_admin = isinstance(message.author, discord.Member) and self._is_admin(message.author)
        if is_admin:
            return False
        return self._is_complainant(channel_id, message.author.id)

    async def _handle_message(self, message: discord.Message):
        channel = message.channel
        channel_id = channel.id
        respond = self._should_respond(channel_id, message)

        # 正在处理中：所有消息统一入队，避免并发修改对话历史
        if channel_id in self.processing_channels:
            self.pending_messages.setdefault(channel_id, []).append(message)
            if respond:
                task = self.generation_tasks.get(channel_id)
                if task and not task.done():
                    task.cancel()
            return

        # 不需要 AI 回复的消息（管理组 / 非投诉人）：仅记录
        if not respond:
            await self._record_only(channel_id, message)
            return

        self.processing_channels.add(channel_id)
        try:
            msgs = [message]
            while msgs:
                await self._run_generation(channel, channel_id, msgs)
                msgs = await self._drain_pending(channel_id)

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
            self.generation_tasks.pop(channel_id, None)

    async def _drain_pending(self, channel_id: int) -> list[discord.Message] | None:
        """从待处理队列中取出消息，非投诉人消息仅记录，返回需要生成回复的投诉人消息"""
        if channel_id not in self.active_channels:
            return None
        pending = self.pending_messages.pop(channel_id, [])
        if not pending:
            return None
        complainant_msgs = []
        for msg in pending:
            if self._should_respond(channel_id, msg):
                complainant_msgs.append(msg)
            else:
                await self._record_only(channel_id, msg)
        return complainant_msgs or None

    # ── 事件监听 ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        """启动（或重连后首次 ready）时从 Discord 恢复分类内频道的对话与管理子区映射"""
        if self._restored_from_discord_once:
            return
        self._restored_from_discord_once = True

        async def _delayed_restore():
            await asyncio.sleep(2)
            await self._restore_auto_reply_state()
            await self._archive_orphan_admin_threads_at_startup()

        asyncio.create_task(_delayed_restore())

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

        # 在管理频道创建 thread
        await self._create_admin_thread(channel)

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

    async def _create_admin_thread(self, channel: discord.TextChannel):
        """在管理频道为新投诉频道创建对应的 thread"""
        if not self.admin_channel_id:
            return
        try:
            admin_ch = self.bot.get_channel(self.admin_channel_id)
            if not admin_ch:
                admin_ch = await self.bot.fetch_channel(self.admin_channel_id)

            thread = await admin_ch.create_thread(
                name=f"新投诉 - {channel.id}",
                type=discord.ChannelType.public_thread,
            )
            self.channel_threads[channel.id] = thread

            await asyncio.sleep(2)
            try:
                await thread.edit(name=channel.name)
            except discord.HTTPException:
                pass

            role_mentions = " ".join(f"<@&{rid}>" for rid in self.allowed_role_ids)
            if role_mentions:
                await thread.send(
                    f"📋 新投诉频道 <#{channel.id}> 已创建\n{role_mentions}"
                )
            else:
                await thread.send(f"📋 新投诉频道 <#{channel.id}> 已创建")

        except Exception as e:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"[AI客服] 创建管理 thread 失败: {e}"
            )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """频道删除时清理资源；若映射过管理子区则归档关闭"""
        thread = self.channel_threads.pop(channel.id, None)
        self.active_channels.discard(channel.id)
        self.channel_complainants.pop(channel.id, None)
        self.conversations.pop(channel.id, None)

        if thread is not None:
            try:
                await thread.edit(archived=True)
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"[AI客服] 投诉频道已删除，已归档管理子区 #{thread.name} ({thread.id})",
                )
            except discord.Forbidden:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"[AI客服] 无权归档管理子区 {thread.id}",
                )
            except discord.HTTPException as e:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"[AI客服] 归档管理子区失败 {thread.id}: {e}",
                )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听活跃频道中的用户消息并自动回复"""
        if message.author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if message.channel.id not in self.active_channels:
            return
        if not self._llm_configured():
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
            if not self._llm_configured():
                await interaction.response.send_message(
                    "❌ 未配置 LLM API Key："
                    "gemini 请设 ai_customer_service.gemini.api_key，"
                    "claude_openai 请设 ai_customer_service.claude_openai.api_key",
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
            self.channel_complainants.pop(channel_id, None)
            self.channel_threads.pop(channel_id, None)
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
