"""
频道管理模块
提供频道名称修改功能
"""
import discord
from discord.ext import commands
from datetime import datetime
from typing import List


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
    
    async def on_config_reload(self):
        """配置重载回调"""
        self.load_config()
    
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
    
    @commands.command(name='改改的名', aliases=['改名', 'rename'])
    @commands.cooldown(1, 300, commands.BucketType.user)  # 每用户5分钟1次
    async def change_channel_name(self, ctx: commands.Context, *, new_name: str):
        """
        修改频道名称
        
        用法: /改改的名 <新频道名>
        
        示例: /改改的名 超级聊天室
        
        注意: 
        - 只能修改配置文件中指定的频道
        - Discord API 限制：每个频道每10分钟最多修改2次名称
        - 频道名称长度限制：1-100 字符
        """
        # 检查用户权限
        if not self.check_role_permission(ctx.author):
            allowed_roles = [
                ctx.guild.get_role(role_id).name 
                for role_id in self.allowed_role_ids 
                if ctx.guild.get_role(role_id)
            ]
            roles_str = "、".join(allowed_roles) if allowed_roles else "无"
            await ctx.send(f"❌ 你没有权限使用此命令！\n需要以下身份组之一: {roles_str}")
            return
        
        # 检查频道权限
        if not self.check_channel_permission(ctx.channel.id):
            await ctx.send("❌ 当前频道不允许修改名称！\n请联系管理员在配置文件中添加此频道。")
            return
        
        # 验证频道名称
        new_name = new_name.strip()
        if not new_name:
            await ctx.send("❌ 频道名称不能为空！")
            return
        
        if len(new_name) > 100:
            await ctx.send("❌ 频道名称太长了！最多 100 个字符。")
            return
        
        if len(new_name) < 1:
            await ctx.send("❌ 频道名称太短了！至少需要 1 个字符。")
            return
        
        # 保存旧名称
        old_name = ctx.channel.name
        
        if old_name == new_name:
            await ctx.send("⚠️ 新名称与当前名称相同！")
            return
        
        # 尝试修改频道名称
        try:
            await ctx.send(f"🔄 正在修改频道名称: `{old_name}` → `{new_name}`")
            await ctx.channel.edit(name=new_name)
            
            # 发送成功消息
            embed = discord.Embed(
                title="✅ 频道名称修改成功",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="原名称", value=f"`{old_name}`", inline=True)
            embed.add_field(name="新名称", value=f"`{new_name}`", inline=True)
            embed.add_field(name="操作者", value=ctx.author.mention, inline=True)
            embed.set_footer(text="注意: Discord API 限制每个频道每10分钟最多修改2次名称")
            
            await ctx.send(embed=embed)
            
            # 记录日志
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"频道名称已修改: {old_name} → {new_name} "
                  f"(操作者: {ctx.author.name}#{ctx.author.discriminator})")
            
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limited
                await ctx.send("❌ 修改失败：Discord API 速率限制\n"
                             "每个频道每10分钟最多只能修改2次名称，请稍后再试。")
            else:
                await ctx.send(f"❌ 修改频道名称失败: {str(e)}")
            
            # 重置冷却
            self.change_channel_name.reset_cooldown(ctx)
            
        except discord.errors.Forbidden:
            await ctx.send("❌ Bot 没有权限修改此频道！\n请确保 Bot 拥有 `管理频道` 权限。")
            self.change_channel_name.reset_cooldown(ctx)
            
        except Exception as e:
            await ctx.send(f"❌ 发生未知错误: {str(e)}")
            self.change_channel_name.reset_cooldown(ctx)
    
    @commands.command(name='频道信息', aliases=['channelinfo', 'chinfo'])
    async def channel_info(self, ctx: commands.Context):
        """
        查看当前频道信息
        
        用法: /频道信息
        """
        # 检查用户权限
        if not self.check_role_permission(ctx.author):
            await ctx.send("❌ 你没有权限使用此命令！")
            return
        
        channel = ctx.channel
        is_allowed = self.check_channel_permission(channel.id)
        
        embed = discord.Embed(
            title=f"📋 频道信息",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="频道名称", value=channel.name, inline=True)
        embed.add_field(name="频道 ID", value=channel.id, inline=True)
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
        
        if channel.topic:
            embed.add_field(name="频道主题", value=channel.topic, inline=False)
        
        await ctx.send(embed=embed)
    
    @commands.command(name='重载', aliases=['reload'])
    @commands.has_permissions(administrator=True)
    async def reload_cog(self, ctx: commands.Context):
        """
        重载频道管理模块（仅管理员）
        
        用法: /重载
        """
        try:
            await ctx.send("🔄 正在重载频道管理模块...")
            await self.bot.reload_extension('cogs.channel_manager')
            await ctx.send("✅ 频道管理模块重载成功！")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Cog 已重载: channel_manager (操作者: {ctx.author.name})")
        except Exception as e:
            await ctx.send(f"❌ 重载失败: {str(e)}")


async def setup(bot):
    """Cog 加载入口"""
    await bot.add_cog(ChannelManager(bot))

