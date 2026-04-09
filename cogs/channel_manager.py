"""
频道管理模块
提供频道名称修改功能（使用斜杠命令）
"""
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from typing import Optional
import asyncio


class ChannelManager(commands.Cog, name="频道管理"):
    """频道管理 Cog"""
    
    def __init__(self, bot):
        self.bot = bot
        self.config_loader = bot.config_loader
        self.load_config()
    
    def load_config(self):
        """加载配置"""
        self.allowed_role_ids = self.config_loader.get('allowed_role_ids', [])
        self.allowed_channel_ids = self.config_loader.get('channel_manager.allowed_channel_ids', [])
        self.cooldown_seconds = self.config_loader.get('channel_manager.cooldown', 300)
        self.default_emoji = self.config_loader.get('channel_manager.default_emoji', '💬')
        self.banned_words_by_channel = self.config_loader.get('channel_manager.banned_words', {})
    
    async def on_config_reload(self):
        """配置重载回调"""
        self.load_config()
    
    def check_banned_words(self, text: str, channel_id: int) -> bool:
        """检查文本中是否包含违禁词"""
        # 获取该频道的违禁词列表
        banned_words = self.banned_words_by_channel.get(channel_id, [])
        if not banned_words:
            return False
        
        text_lower = text.lower()
        for word in banned_words:
            if word.lower() in text_lower:
                return True
        return False
    
    def check_role_permission(self, member: discord.Member) -> bool:
        """检查用户是否有权限使用命令"""
        if not self.allowed_role_ids:
            return True  # 如果没有配置身份组，允许所有人使用
        
        member_role_ids = [role.id for role in member.roles]
        return any(role_id in member_role_ids for role_id in self.allowed_role_ids)
    
    def check_channel_permission(self, channel_id: int) -> bool:
        """检查频道是否允许被修改"""
        if not self.allowed_channel_ids:
            return False  # 如果没有配置频道，不允许修改任何频道
        
        return channel_id in self.allowed_channel_ids
    
    @app_commands.command(name="改改的名", description="修改当前频道的名称")
    @app_commands.describe(
        新频道名="要设置的新频道名称（1-100个字符）",
        emoji="频道名称前的emoji（可选，不填则使用默认emoji）"
    )
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.user.id)
    async def change_channel_name(self, interaction: discord.Interaction, 新频道名: str, emoji: Optional[str] = None):
        """修改频道名称的斜杠命令"""
        
        # 检查频道权限
        if not self.check_channel_permission(interaction.channel.id):
            await interaction.response.send_message(
                "❌ 当前频道不允许修改名称！\n请联系管理员在配置文件中添加此频道。",
                ephemeral=True
            )
            return
        
        # 验证频道名称
        new_name = 新频道名.strip()
        if not new_name:
            await interaction.response.send_message("❌ 频道名称不能为空！", ephemeral=True)
            return
        
        # 检查违禁词
        if self.check_banned_words(new_name, interaction.channel.id):
            await interaction.response.send_message(
                "呜哇！这个太色情了，我不看我不看"
            )
            return
        
        # 处理emoji前缀
        channel_emoji = emoji if emoji else self.default_emoji
        final_name = f"{channel_emoji}丨{new_name}"
        
        if len(final_name) > 100:
            await interaction.response.send_message(
                f"❌ 频道名称太长了！当前 {len(final_name)} 个字符，最多 100 个字符。",
                ephemeral=True
            )
            return
        
        if len(new_name) < 1:
            await interaction.response.send_message("❌ 频道名称太短了！至少需要 1 个字符。", ephemeral=True)
            return
        
        # 保存旧名称
        channel = interaction.channel
        old_name = channel.name
        
        if old_name == final_name:
            await interaction.response.send_message("⚠️ 新名称与当前名称相同！", ephemeral=True)
            return
        
        # 先响应，避免超时
        await interaction.response.send_message(f"🔄 正在修改频道名称: `{old_name}` → `{final_name}`")
        
        # 尝试修改频道名称（设置3秒超时，避免被速率限制阻塞）
        try:
            await asyncio.wait_for(channel.edit(name=final_name), timeout=3.0)
            
            # 发送成功消息
            embed = discord.Embed(
                title="✅ 频道名称修改成功",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="原名称", value=f"`{old_name}`", inline=True)
            embed.add_field(name="新名称", value=f"`{final_name}`", inline=True)
            embed.add_field(name="操作者", value=interaction.user.mention, inline=True)
            if emoji:
                embed.add_field(name="自定义Emoji", value=emoji, inline=True)
            else:
                embed.add_field(name="默认Emoji", value=self.default_emoji, inline=True)
            embed.set_footer(text="注意: Discord API 限制每个频道每10分钟最多修改2次名称")
            
            await interaction.followup.send(embed=embed)
            
            # 记录日志
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"频道名称已修改: {old_name} → {final_name} "
                  f"(操作者: {interaction.user.name})")
            
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "❌ 修改失败：Discord API 速率限制\n"
                "每个频道每10分钟最多只能修改2次名称，请稍后再试。"
            )
            
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limited
                await interaction.followup.send(
                    "❌ 修改失败：Discord API 速率限制\n"
                    "每个频道每10分钟最多只能修改2次名称，请稍后再试。"
                )
            else:
                await interaction.followup.send(f"❌ 修改频道名称失败: {str(e)}")
            
        except discord.errors.Forbidden:
            await interaction.followup.send(
                "❌ Bot 没有权限修改此频道！\n请确保 Bot 拥有 `管理频道` 权限。"
            )
            
        except Exception as e:
            await interaction.followup.send(f"❌ 发生未知错误: {str(e)}")
    
    @app_commands.command(name="频道信息", description="查看当前频道的详细信息")
    async def channel_info(self, interaction: discord.Interaction):
        """查看频道信息的斜杠命令"""
        
        # 检查用户权限
        if not self.check_role_permission(interaction.user):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令！",
                ephemeral=True
            )
            return
        
        channel = interaction.channel
        is_allowed = self.check_channel_permission(channel.id)
        
        embed = discord.Embed(
            title=f"📋 频道信息",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="频道名称", value=channel.name, inline=True)
        embed.add_field(name="频道 ID", value=str(channel.id), inline=True)
        embed.add_field(
            name="可修改名称", 
            value="✅ 是" if is_allowed else "❌ 否", 
            inline=True
        )
        embed.add_field(name="频道类型", value=str(channel.type), inline=True)
        embed.add_field(
            name="创建时间", 
            value=channel.created_at.strftime("%Y-%m-%d %H:%M:%S"), 
            inline=True
        )
        
        if hasattr(channel, 'topic') and channel.topic:
            embed.add_field(name="频道主题", value=channel.topic, inline=False)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="重载模块", description="重载频道管理模块（仅管理员）")
    @app_commands.checks.has_permissions(administrator=True)
    async def reload_cog(self, interaction: discord.Interaction):
        """重载模块的斜杠命令"""
        try:
            await interaction.response.send_message("🔄 正在重载频道管理模块...")
            await self.bot.reload_extension('cogs.channel_manager')
            
            # 重新同步命令
            synced = await self.bot.tree.sync()
            
            await interaction.followup.send(
                f"✅ 频道管理模块重载成功！\n已同步 {len(synced)} 个命令。"
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Cog 已重载: channel_manager (操作者: {interaction.user.name})")
        except Exception as e:
            await interaction.followup.send(f"❌ 重载失败: {str(e)}")
    
    # 错误处理
    @change_channel_name.error
    async def change_channel_name_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """处理改名命令的错误"""
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏰ 命令冷却中，请等待 {error.retry_after:.1f} 秒后再试。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ 命令执行出错: {str(error)}",
                ephemeral=True
            )
    
    @reload_cog.error
    async def reload_cog_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """处理重载命令的错误"""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ 你没有权限使用此命令！需要管理员权限。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ 命令执行出错: {str(error)}",
                ephemeral=True
            )


async def setup(bot):
    """Cog 加载入口"""
    await bot.add_cog(ChannelManager(bot))
