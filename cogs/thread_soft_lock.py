"""
线程软锁定模块
将线程软锁定后，所有不在白名单身份组中的用户发言会被立即删除；
管理组与本帖楼主可执行软锁定相关命令，楼主发言不会被删除。
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from typing import Optional
import json
import os


class ThreadSoftLock(commands.Cog, name="线程软锁定"):
    """线程软锁定 Cog"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config_loader = bot.config_loader
        self.allowed_role_ids = self.config_loader.get('allowed_role_ids', [])
        self.data_file = "data/thread_soft_lock.json"
        self.locked_threads = {}  # {thread_id: {"whitelist_roles": [role_id, ...], "locked_by": user_id, "locked_at": timestamp}}
        self.load_data()

    async def on_config_reload(self):
        self.allowed_role_ids = self.config_loader.get('allowed_role_ids', [])

    def _is_staff(self, member: discord.Member) -> bool:
        """管理组：管理员或配置中的 allowed_role_ids"""
        if member.guild_permissions.administrator:
            return True
        if not self.allowed_role_ids:
            return False
        member_role_ids = [role.id for role in member.roles]
        return any(rid in member_role_ids for rid in self.allowed_role_ids)

    def _is_thread_owner(self, thread: discord.Thread, user_id: int) -> bool:
        return thread.owner_id is not None and thread.owner_id == user_id

    def _can_use_soft_lock_commands(self, member: discord.Member, thread: discord.Thread) -> bool:
        return self._is_staff(member) or self._is_thread_owner(thread, member.id)
    
    def load_data(self):
        """从文件加载持久化数据"""
        try:
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 将字符串键转换回整数
                    self.locked_threads = {int(k): v for k, v in data.items()}
        except Exception as e:
            print(f"[ThreadSoftLock] 加载数据失败: {e}")
            self.locked_threads = {}
    
    def save_data(self):
        """保存数据到文件"""
        try:
            os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.locked_threads, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[ThreadSoftLock] 保存数据失败: {e}")
    
    def is_user_whitelisted(self, member: discord.Member, thread: discord.Thread) -> bool:
        """检查用户是否在白名单中（含管理员与本帖楼主豁免）"""
        thread_id = thread.id
        if thread_id not in self.locked_threads:
            return True
        
        # 管理员始终在白名单中
        if member.guild_permissions.administrator:
            return True

        # 论坛帖 / 线程创建者（楼主）豁免删除
        if self._is_thread_owner(thread, member.id):
            return True
        
        whitelist_roles = self.locked_threads[thread_id].get("whitelist_roles", [])
        
        # 如果没有设置白名单身份组，则只有管理员可以发言
        if not whitelist_roles:
            return False
        
        member_role_ids = [role.id for role in member.roles]
        return any(role_id in member_role_ids for role_id in whitelist_roles)
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """监听消息，删除非白名单用户在软锁定线程中的发言"""
        # 忽略机器人消息
        if message.author.bot:
            return
        
        # 检查是否在线程中
        if not isinstance(message.channel, discord.Thread):
            return
        
        thread_id = message.channel.id
        
        # 检查线程是否被软锁定
        if thread_id not in self.locked_threads:
            return
        
        # 检查用户是否在白名单中
        if not self.is_user_whitelisted(message.author, message.channel):
            try:
                await message.delete()
                print(f"[ThreadSoftLock] 已删除 {message.author.name} 在软锁定线程 {message.channel.name} 中的消息")
            except discord.Forbidden:
                print(f"[ThreadSoftLock] 无权删除消息: {message.channel.name}")
            except discord.NotFound:
                pass
            except Exception as e:
                print(f"[ThreadSoftLock] 删除消息失败: {e}")
    
    soft_lock_group = app_commands.Group(
        name="软锁定",
        description="线程软锁定相关命令（管理组或本帖楼主）",
        default_permissions=None,
    )

    async def _ensure_soft_lock_command_user(
        self, interaction: discord.Interaction
    ) -> Optional[discord.Thread]:
        """在线程内且为管理组或楼主时返回线程，否则回复错误并返回 None。"""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ 此命令只能在线程中使用！",
                ephemeral=True,
            )
            return None
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ 此命令仅在服务器内有效。",
                ephemeral=True,
            )
            return None
        thread = interaction.channel
        if not self._can_use_soft_lock_commands(interaction.user, thread):
            await interaction.response.send_message(
                "❌ 仅管理组或本帖楼主可使用此命令。",
                ephemeral=True,
            )
            return None
        return thread
    
    @soft_lock_group.command(name="锁定", description="软锁定当前线程")
    async def lock_thread(self, interaction: discord.Interaction):
        """软锁定当前线程"""
        thread = await self._ensure_soft_lock_command_user(interaction)
        if thread is None:
            return

        thread_id = thread.id
        
        # 检查是否已经锁定
        if thread_id in self.locked_threads:
            await interaction.response.send_message(
                "❌ 此线程已经被软锁定！",
                ephemeral=True
            )
            return
        
        # 锁定线程
        self.locked_threads[thread_id] = {
            "whitelist_roles": [],
            "locked_by": interaction.user.id,
            "locked_at": datetime.now().isoformat()
        }
        self.save_data()
        
        embed = discord.Embed(
            title="🔒 线程已软锁定",
            description="此线程已被软锁定，仅管理员、白名单身份组成员与本帖楼主可发言。",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="操作者", value=interaction.user.mention, inline=True)
        embed.add_field(name="白名单身份组", value="暂无", inline=True)
        embed.set_footer(text="使用 /软锁定 添加白名单 来添加白名单身份组")
        
        await interaction.response.send_message(embed=embed)
        
        print(f"[ThreadSoftLock] {interaction.user.name} 软锁定了线程 {interaction.channel.name}")
    
    @soft_lock_group.command(name="解锁", description="解除当前线程的软锁定")
    async def unlock_thread(self, interaction: discord.Interaction):
        """解除软锁定"""
        thread = await self._ensure_soft_lock_command_user(interaction)
        if thread is None:
            return

        thread_id = thread.id
        
        # 检查是否已锁定
        if thread_id not in self.locked_threads:
            await interaction.response.send_message(
                "❌ 此线程未被软锁定！",
                ephemeral=True
            )
            return
        
        # 解锁线程
        del self.locked_threads[thread_id]
        self.save_data()
        
        embed = discord.Embed(
            title="🔓 线程已解锁",
            description="此线程的软锁定已解除，所有用户都可以正常发言。",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="操作者", value=interaction.user.mention, inline=True)
        
        await interaction.response.send_message(embed=embed)
        
        print(f"[ThreadSoftLock] {interaction.user.name} 解锁了线程 {interaction.channel.name}")
    
    @soft_lock_group.command(name="添加白名单", description="添加白名单身份组")
    @app_commands.describe(身份组="要添加到白名单的身份组")
    async def add_whitelist_role(
        self,
        interaction: discord.Interaction,
        身份组: discord.Role
    ):
        """添加白名单身份组"""
        thread = await self._ensure_soft_lock_command_user(interaction)
        if thread is None:
            return

        thread_id = thread.id
        
        # 检查是否已锁定
        if thread_id not in self.locked_threads:
            await interaction.response.send_message(
                "❌ 此线程未被软锁定！请先使用 `/软锁定 锁定` 命令锁定线程。",
                ephemeral=True
            )
            return
        
        # 检查是否已在白名单中
        if 身份组.id in self.locked_threads[thread_id]["whitelist_roles"]:
            await interaction.response.send_message(
                f"❌ {身份组.mention} 已经在白名单中！",
                ephemeral=True
            )
            return
        
        # 添加到白名单
        self.locked_threads[thread_id]["whitelist_roles"].append(身份组.id)
        self.save_data()
        
        # 获取所有白名单身份组
        whitelist_mentions = []
        for role_id in self.locked_threads[thread_id]["whitelist_roles"]:
            role = interaction.guild.get_role(role_id)
            if role:
                whitelist_mentions.append(role.mention)
        
        embed = discord.Embed(
            title="✅ 已添加白名单身份组",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="添加的身份组", value=身份组.mention, inline=True)
        embed.add_field(name="当前白名单", value="\n".join(whitelist_mentions) or "无", inline=False)
        
        await interaction.response.send_message(embed=embed)
        
        print(f"[ThreadSoftLock] {interaction.user.name} 将 {身份组.name} 添加到线程 {interaction.channel.name} 的白名单")
    
    @soft_lock_group.command(name="删除白名单", description="删除白名单身份组")
    @app_commands.describe(身份组="要从白名单移除的身份组")
    async def remove_whitelist_role(
        self,
        interaction: discord.Interaction,
        身份组: discord.Role
    ):
        """删除白名单身份组"""
        thread = await self._ensure_soft_lock_command_user(interaction)
        if thread is None:
            return

        thread_id = thread.id
        
        # 检查是否已锁定
        if thread_id not in self.locked_threads:
            await interaction.response.send_message(
                "❌ 此线程未被软锁定！",
                ephemeral=True
            )
            return
        
        # 检查是否在白名单中
        if 身份组.id not in self.locked_threads[thread_id]["whitelist_roles"]:
            await interaction.response.send_message(
                f"❌ {身份组.mention} 不在白名单中！",
                ephemeral=True
            )
            return
        
        # 从白名单移除
        self.locked_threads[thread_id]["whitelist_roles"].remove(身份组.id)
        self.save_data()
        
        # 获取剩余白名单身份组
        whitelist_mentions = []
        for role_id in self.locked_threads[thread_id]["whitelist_roles"]:
            role = interaction.guild.get_role(role_id)
            if role:
                whitelist_mentions.append(role.mention)
        
        embed = discord.Embed(
            title="✅ 已移除白名单身份组",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="移除的身份组", value=身份组.mention, inline=True)
        embed.add_field(name="当前白名单", value="\n".join(whitelist_mentions) or "无", inline=False)
        
        await interaction.response.send_message(embed=embed)
        
        print(f"[ThreadSoftLock] {interaction.user.name} 将 {身份组.name} 从线程 {interaction.channel.name} 的白名单移除")
    
    @soft_lock_group.command(name="状态", description="查看当前线程的软锁定状态")
    async def lock_status(self, interaction: discord.Interaction):
        """查看软锁定状态"""
        thread = await self._ensure_soft_lock_command_user(interaction)
        if thread is None:
            return

        thread_id = thread.id
        
        # 检查是否已锁定
        if thread_id not in self.locked_threads:
            embed = discord.Embed(
                title="🔓 线程未软锁定",
                description="此线程未被软锁定，所有用户都可以正常发言。",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        lock_info = self.locked_threads[thread_id]
        
        # 获取锁定者
        locked_by = interaction.guild.get_member(lock_info["locked_by"])
        locked_by_text = locked_by.mention if locked_by else f"用户ID: {lock_info['locked_by']}"
        
        # 获取白名单身份组
        whitelist_mentions = []
        for role_id in lock_info["whitelist_roles"]:
            role = interaction.guild.get_role(role_id)
            if role:
                whitelist_mentions.append(role.mention)
        
        embed = discord.Embed(
            title="🔒 线程软锁定状态",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="锁定者", value=locked_by_text, inline=True)
        embed.add_field(name="锁定时间", value=lock_info.get("locked_at", "未知"), inline=True)
        embed.add_field(
            name="白名单身份组",
            value="\n".join(whitelist_mentions) if whitelist_mentions else "无（管理员与本帖楼主可发言）",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    """Cog 加载入口"""
    await bot.add_cog(ThreadSoftLock(bot))

