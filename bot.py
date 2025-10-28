"""
Discord Bot 主文件
支持模块化 Cog 和热重载
"""
import discord
from discord.ext import commands
import asyncio
import sys
from pathlib import Path
from datetime import datetime
from utils import ConfigLoader


class HumanoidBot(commands.Bot):
    """主 Bot 类"""
    
    def __init__(self, config_loader: ConfigLoader):
        self.config_loader = config_loader
        
        # 设置 Intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix=config_loader.get('prefix', '/'),
            intents=intents,
            help_command=None  # 使用自定义帮助命令
        )
        
        self.initial_extensions = [
            'cogs.channel_manager',
        ]
        
    async def setup_hook(self):
        """Bot 启动时的设置"""
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始加载扩展模块...")
        
        # 加载所有 Cog
        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
                print(f"  ✓ 已加载: {extension}")
            except Exception as e:
                print(f"  ✗ 加载失败 {extension}: {e}")
        
        # 同步斜杠命令到 Discord
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 正在同步斜杠命令...")
        try:
            synced = await self.tree.sync()
            print(f"  ✓ 已同步 {len(synced)} 个斜杠命令")
        except Exception as e:
            print(f"  ✗ 命令同步失败: {e}")
        
        # 启动配置文件监控
        if self.config_loader.get('hot_reload.enabled', True):
            interval = self.config_loader.get('hot_reload.watch_interval', 2)
            await self.config_loader.start_watching(interval)
        
        # 添加配置重载回调
        self.config_loader.add_reload_callback(self.on_config_reload)
        
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 设置完成")
    
    async def on_ready(self):
        """Bot 就绪事件"""
        print(f"\n{'='*50}")
        print(f"Bot 已登录: {self.user.name} (ID: {self.user.id})")
        print(f"discord.py 版本: {discord.__version__}")
        print(f"已连接服务器数: {len(self.guilds)}")
        print(f"斜杠命令数: {len(self.tree.get_commands())}")
        print(f"{'='*50}\n")
        
        # 设置 Bot 状态
        await self.change_presence(
            activity=discord.Game(name="使用 /改改的名 修改频道")
        )
    
    async def on_config_reload(self):
        """配置重载回调"""
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 配置已更新，通知所有模块...")
        
        # 通知所有 Cog 配置已更新
        for cog_name, cog in self.cogs.items():
            if hasattr(cog, 'on_config_reload'):
                try:
                    await cog.on_config_reload()
                    print(f"  ✓ {cog_name} 配置已更新")
                except Exception as e:
                    print(f"  ✗ {cog_name} 配置更新失败: {e}")
    
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        """全局错误处理"""
        if isinstance(error, commands.CommandNotFound):
            return  # 忽略未找到的命令
        
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ 你没有权限使用这个命令！")
            return
        
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ 缺少必需参数: {error.param.name}")
            return
        
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏰ 命令冷却中，请等待 {error.retry_after:.1f} 秒")
            return
        
        # 其他错误
        print(f"命令错误 [{ctx.command}]: {error}")
        await ctx.send(f"❌ 执行命令时出错: {str(error)}")


async def main():
    """主函数"""
    print(f"\n{'='*50}")
    print("Humanoid Bot - Discord 娱乐机器人")
    print(f"{'='*50}\n")
    
    # 加载配置
    try:
        config_loader = ConfigLoader('config/config.yaml')
        config_loader.load_config()
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        print("\n请确保 config/config.yaml 文件存在且格式正确")
        print("你可以复制 config/config.example.yaml 作为模板")
        sys.exit(1)
    
    # 检查 Token
    token = config_loader.get('token')
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        print("❌ 请在 config/config.yaml 中设置正确的 Bot Token")
        sys.exit(1)
    
    # 创建并运行 Bot
    bot = HumanoidBot(config_loader)
    
    try:
        await bot.start(token)
    except KeyboardInterrupt:
        print("\n正在关闭 Bot...")
    except Exception as e:
        print(f"❌ Bot 运行错误: {e}")
    finally:
        if not bot.is_closed():
            await bot.close()
        config_loader.stop_watching()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot 已停止")

