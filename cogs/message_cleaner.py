"""
一键冲水模块
删除指定用户在指定范围（频道、时间）内的所有消息
"""
import discord
from discord import app_commands
from discord.abc import Snowflake
from discord.ext import commands
from datetime import datetime
from typing import Optional
import asyncio
import aiohttp
from collections import deque


class MessageCleaner(commands.Cog, name="一键冲水"):
    """一键冲水 Cog"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_loader = bot.config_loader
        self.load_config()
        
        # 存储正在运行的清理任务
        self.active_tasks = {}
        
    def load_config(self):
        """加载配置"""
        self.user_token = self.config_loader.get('message_cleaner.user_token', '')
        self.search_interval = self.config_loader.get('message_cleaner.search_interval', 5)
        self.delete_batch_size = self.config_loader.get('message_cleaner.delete_batch_size', 10)
        self.allowed_role_ids = self.config_loader.get('allowed_role_ids', [])
        self.max_messages = self.config_loader.get('message_cleaner.max_messages', 10000)
    
    async def on_config_reload(self):
        """配置重载回调"""
        self.load_config()
    
    def check_role_permission(self, member: discord.Member) -> bool:
        """检查用户是否有权限使用命令"""
        if not self.allowed_role_ids:
            return True
        
        if member.guild_permissions.administrator:
            return True

        member_role_ids = [role.id for role in member.roles]
        return any(role_id in member_role_ids for role_id in self.allowed_role_ids)
    
    async def search_messages(self, guild_id: int, author_id: int, channel_id: Optional[int], 
                             message_queue: deque, stop_event: asyncio.Event, 
                             progress_data: dict):
        """搜索消息的协程"""
        headers = {
            'Authorization': self.user_token,
            'Content-Type': 'application/json'
        }
        
        total_messages = -1
        current_max_id = -1
        search_count = 0
        
        async with aiohttp.ClientSession() as session:
            while not stop_event.is_set():
                try:
                    # 构造搜索 URL
                    url = (
                        f"https://discord.com/api/v9/guilds/{guild_id}/messages/search"
                        f"?author_id={author_id}"
                        f"&sort_by=timestamp"
                        f"&sort_order=desc"
                        f"&offset=0"
                        f"&include_nsfw=true"
                    )
                    
                    # 如果指定了频道，添加频道参数
                    if channel_id:
                        url += f"&channel_id={channel_id}"
                    
                    if current_max_id != -1:
                        url += f"&max_id={current_max_id}"
                    
                    # 发送请求
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            messages = data.get('messages', [])
                            
                            if not messages:
                                if progress_data.get('searched', 0) >= total_messages:
                                    # 没有更多消息了
                                    progress_data['search_finished'] = True
                                    break
                                else:
                                    if progress_data.get('search_paused', 0) >= 3:
                                        progress_data['search_finished'] = True
                                        break
                                    progress_data['search_paused'] = progress_data.get('search_paused', 0) + 1
                                    await asyncio.sleep(15)
                                    continue
                            if total_messages == -1:
                                total_messages = data.get('total_results', 0)
                            # 处理搜索结果
                            found_count = 0
                            for message_group in messages:
                                for message in message_group:
                                    message_id = int(message['id'])
                                    channel_id = int(message['channel_id'])
                                    
                                    # 检查是否是帖子首楼
                                    if channel_id == message_id:
                                        # 跳过帖子首楼
                                        progress_data['skipped_threads'] += 1
                                        found_count += 1
                                        if message_id < current_max_id or current_max_id == -1:
                                            current_max_id = message_id
                                        continue

                                    # 检查是否被标注
                                    if message.get('pinned', False):
                                        # 跳过被标注的消息
                                        progress_data['skipped_pinned'] += 1
                                        found_count += 1
                                        
                                        if message_id < current_max_id or current_max_id == -1:
                                            current_max_id = message_id
                                        continue

                                    # 添加到队列
                                    message_info = {
                                        'id': message_id,
                                        'channel_id': int(message['channel_id']),
                                        'content': message.get('content', '')[:50]  # 只保存前50个字符
                                    }
                                    message_queue.append(message_info)
                                    found_count += 1
                                    
                                    # 更新 max_id 为最旧的消息 ID
                                    if message_id < current_max_id or current_max_id == -1:
                                        current_max_id = message_id
                            
                            search_count += 1
                            progress_data['searched'] += found_count
                            progress_data['last_search_time'] = datetime.now()

                            # 搜不到则退出
                            if found_count == 0:
                                if progress_data.get('searched', 0) >= total_messages:
                                    # 没有更多消息了
                                    progress_data['search_finished'] = True
                                    break
                                else:
                                    if progress_data.get('search_paused', 0) >= 3:
                                        progress_data['search_finished'] = True
                                        break
                                    progress_data['search_paused'] = progress_data.get('search_paused', 0) + 1
                                    await asyncio.sleep(15)
                                    continue
                            
                            # 检查是否达到最大消息数
                            if progress_data['searched'] >= self.max_messages:
                                progress_data['search_finished'] = True
                                break
                            
                        elif response.status == 429:
                            # 速率限制
                            retry_after = int(response.headers.get('Retry-After', 10))
                            progress_data['rate_limited'] = True
                            await asyncio.sleep(retry_after)
                            progress_data['rate_limited'] = False
                            
                        elif response.status == 401:
                            # Token 无效
                            progress_data['error'] = 'User Token 无效，请检查配置'
                            break
                            
                        else:
                            # 其他错误
                            progress_data['error'] = f'API 错误: {response.status}'
                            break
                    
                    # 等待下一次搜索
                    await asyncio.sleep(self.search_interval)
                    
                except Exception as e:
                    progress_data['error'] = f'搜索错误: {str(e)}'
                    break
        
        progress_data['search_finished'] = True
    
    async def delete_messages(self, message_queue: deque, stop_event: asyncio.Event, 
                             progress_data: dict, interaction: discord.Interaction):
        """删除消息的协程"""
        while not stop_event.is_set():
            try:
                # 等待队列中有消息
                if not message_queue:
                    # 如果搜索已完成且队列为空，退出
                    if progress_data.get('search_finished', False):
                        break
                    await asyncio.sleep(1)
                    continue
                
                # 批量删除消息
                batch = []
                batch_per_channel = {}
                while message_queue and len(batch) < self.delete_batch_size:
                    batch.append(message_queue.popleft())
                
                for msg_info in batch:
                    batch_per_channel.setdefault(msg_info['channel_id'], []).append(discord.Object(msg_info['id']))
                
                for channel_id, message_ids in batch_per_channel.items():
                    channel = self.bot.get_channel(channel_id)
                    if not channel:
                        channel = await self.bot.fetch_channel(channel_id)
                    should_archive = False
                    if isinstance(channel, discord.Thread) and channel.archived:
                        should_archive = True
                        await channel.edit(archived=False)
                    try:
                        await channel.delete_messages(message_ids)
                        print(f"删除消息成功: {message_ids}")
                        
                        progress_data['deleted'] += len(message_ids)
                        progress_data['last_delete_time'] = datetime.now()
                    except Exception as e:
                        for message_id in message_ids:
                            try:
                                await channel.delete_messages([message_id])
                                print(f"删除消息成功: {message_id}")
                                progress_data['deleted'] += 1
                                progress_data['last_delete_time'] = datetime.now()
                            except Exception as e:
                                print(f"删除消息失败: {e}")
                                progress_data['forbidden'] += 1
                                progress_data['last_delete_time'] = datetime.now()
                    if should_archive:
                        await channel.edit(archived=True)

            except Exception as e:
                progress_data['error'] = f'删除错误: {str(e)}'
                break
        
    
    async def update_progress_embed(self, interaction: discord.Interaction, progress_data: dict, 
                                   message: discord.Message):
        """更新进度 Embed"""
        while not progress_data.get('finished', False):
            try:
                embed = discord.Embed(
                    title="🚽 一键冲水进行中...",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                
                # 搜索进度
                search_status = "✅ 完成" if progress_data.get('search_finished', False) else "🔄 进行中"
                if progress_data.get('rate_limited', False):
                    search_status = "⏰ 速率限制中"
                
                embed.add_field(
                    name="搜索状态",
                    value=search_status,
                    inline=True
                )
                
                embed.add_field(
                    name="已搜索",
                    value=f"`{progress_data['searched']}` 条消息",
                    inline=True
                )
                
                embed.add_field(
                    name="队列中",
                    value=f"`{len(progress_data['queue'])}` 条消息",
                    inline=True
                )
                
                # 删除进度
                embed.add_field(
                    name="已删除",
                    value=f"`{progress_data['deleted']}` 条消息",
                    inline=True
                )
                
                embed.add_field(
                    name="无法删除",
                    value=f"`{progress_data['forbidden']}` 条消息",
                    inline=True
                )
                
                embed.add_field(
                    name="跳过帖子首楼",
                    value=f"`{progress_data['skipped_threads']}` 条消息",
                    inline=True
                )
                
                # 错误信息
                if progress_data.get('error'):
                    embed.add_field(
                        name="❌ 错误",
                        value=progress_data['error'],
                        inline=False
                    )
                
                # 时间信息
                if progress_data.get('last_search_time'):
                    embed.add_field(
                        name="最后搜索",
                        value=progress_data['last_search_time'].strftime("%H:%M:%S"),
                        inline=True
                    )
                
                if progress_data.get('last_delete_time'):
                    embed.add_field(
                        name="最后删除",
                        value=progress_data['last_delete_time'].strftime("%H:%M:%S"),
                        inline=True
                    )
                
                # 进度条
                total = progress_data['searched']
                deleted = progress_data['deleted']
                if total > 0:
                    percentage = (deleted / total) * 100
                    bar_length = 20
                    filled = int(bar_length * deleted / total)
                    bar = "█" * filled + "░" * (bar_length - filled)
                    embed.add_field(
                        name="删除进度",
                        value=f"{bar} `{percentage:.1f}%`",
                        inline=False
                    )
                
                embed.set_footer(text="每5秒更新一次 | 按 🛑 停止")
                
                await message.edit(embed=embed)
                await asyncio.sleep(5)
                
            except Exception as e:
                print(f"更新进度失败: {e}")
                message = await message.channel.fetch_message(message.id)
                await asyncio.sleep(5)
    
    @app_commands.command(name="一键冲水", description="删除指定用户在指定范围内的所有消息")
    @app_commands.describe(
        用户="要删除消息的用户",
        频道="指定频道（留空则搜索整个服务器）"
    )
    async def flush_messages(
        self, 
        interaction: discord.Interaction, 
        用户: discord.Member,
        频道: Optional[discord.abc.GuildChannel] = None
    ):
        """一键冲水命令"""
        
        # 检查用户权限
        if not self.check_role_permission(interaction.user):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令！",
                ephemeral=True
            )
            return
        
        # 检查 User Token
        if not self.user_token:
            await interaction.response.send_message(
                "❌ 未配置 User Token，请在配置文件中添加！",
                ephemeral=True
            )
            return
        
        # 创建初始 Embed
        embed = discord.Embed(
            title="🚽 一键冲水启动中...",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="目标用户", value=用户.mention, inline=True)
        embed.add_field(
            name="搜索范围", 
            value=频道.mention if 频道 else "整个服务器", 
            inline=True
        )
        
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        
        # 创建任务数据
        task_id = f"{interaction.guild.id}_{用户.id}_{datetime.now().timestamp()}"
        message_queue = deque()
        stop_event = asyncio.Event()
        
        progress_data = {
            'searched': 0,
            'deleted': 0,
            'forbidden': 0,
            'not_found': 0,
            'delete_errors': 0,
            'skipped_threads': 0,
            'search_finished': False,
            'rate_limited': False,
            'finished': False,
            'queue': message_queue,
            'last_search_time': None,
            'last_delete_time': None,
            'error': None
        }
        
        # 启动搜索和删除任务
        search_task = asyncio.create_task(
            self.search_messages(
                interaction.guild.id,
                用户.id,
                频道.id if 频道 else None,
                message_queue,
                stop_event,
                progress_data
            )
        )
        
        delete_task = asyncio.create_task(
            self.delete_messages(
                message_queue,
                stop_event,
                progress_data,
                interaction
            )
        )
        
        progress_task = asyncio.create_task(
            self.update_progress_embed(
                interaction,
                progress_data,
                message
            )
        )
        
        # 保存任务信息
        self.active_tasks[task_id] = {
            'search_task': search_task,
            'delete_task': delete_task,
            'progress_task': progress_task,
            'stop_event': stop_event,
            'progress_data': progress_data
        }
        
        # 等待任务完成
        try:
            await asyncio.gather(search_task, delete_task)
        except Exception as e:
            print(f"任务执行错误: {e}")
        finally:
            progress_data['finished'] = True
            await asyncio.sleep(1)  # 等待最后一次进度更新
            progress_task.cancel()
            
            # 发送完成消息
            final_embed = discord.Embed(
                title="✅ 一键冲水完成！",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            final_embed.add_field(name="目标用户", value=用户.mention, inline=True)
            final_embed.add_field(name="已搜索", value=f"`{progress_data['searched']}` 条", inline=True)
            final_embed.add_field(name="已删除", value=f"`{progress_data['deleted']}` 条", inline=True)
            final_embed.add_field(name="无法删除", value=f"`{progress_data['forbidden']}` 条", inline=True)
            final_embed.add_field(name="跳过帖子", value=f"`{progress_data['skipped_threads']}` 条", inline=True)
            
            if progress_data.get('error'):
                final_embed.add_field(name="错误", value=progress_data['error'], inline=False)
            
            await message.edit(embed=final_embed)
            
            # 清理任务
            del self.active_tasks[task_id]
            
            # 记录日志
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"一键冲水完成: 用户={用户.name}, "
                  f"搜索={progress_data['searched']}, "
                  f"删除={progress_data['deleted']} "
                  f"(操作者: {interaction.user.name})")
    
    @app_commands.command(name="停止冲水", description="停止当前正在运行的冲水任务")
    async def stop_flush(self, interaction: discord.Interaction):
        """停止冲水任务"""
        
        if not self.active_tasks:
            await interaction.response.send_message(
                "❌ 当前没有正在运行的冲水任务！",
                ephemeral=True
            )
            return
        
        # 停止所有任务
        stopped_count = 0
        for task_id, task_info in self.active_tasks.items():
            task_info['stop_event'].set()
            stopped_count += 1
        
        await interaction.response.send_message(
            f"✅ 已停止 {stopped_count} 个冲水任务！",
            ephemeral=True
        )


async def setup(bot):
    """Cog 加载入口"""
    await bot.add_cog(MessageCleaner(bot))

