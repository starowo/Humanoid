# Humanoid Bot 🤖

一个模块化的 Discord 娱乐机器人，支持热重载和配置自动更新。

## ✨ 特性

- 🔌 **模块化设计** - 基于 discord.py Cog 系统，易于扩展
- 🔥 **热重载支持** - 支持配置文件和模块热重载，无需重启
- ⚙️ **配置自动更新** - 自动监控配置文件变化并实时更新
- 🔐 **权限控制** - 基于身份组的命令权限管理
- 📝 **频道管理** - 轻松修改频道名称

## 📋 功能列表

### 频道管理模块

- `/改改的名 新频道名:<新名称>` - 修改指定频道的名称（斜杠命令）
- `/频道信息` - 查看当前频道信息
- `/重载模块` - 重载频道管理模块（仅管理员）

### 一键冲水模块 🚽

- `/一键冲水 用户:<@用户>` - 删除指定用户在指定范围内的所有消息
  - `用户` - 要删除消息的目标用户（必填）
  - `起始消息id` - 从此消息ID开始搜索（可选，默认从最新消息开始）
  - `频道` - 指定频道（可选，不填则搜索整个服务器）
- `/停止冲水` - 停止当前正在运行的冲水任务

**特点：**
- 🔍 **智能搜索** - 使用 Discord API v9 搜索接口，快速定位消息
- 🧵 **双线程并行** - 搜索和删除同时进行，效率更高
- 📊 **实时进度** - 可视化 Embed 显示详细进度信息
- 🛡️ **安全保护** - 自动跳过帖子首楼，防止删除重要内容
- ⏸️ **可中断** - 可随时停止正在运行的任务

**注意：** 所有命令都是 Discord 原生斜杠命令，输入 `/` 后会自动显示命令列表和参数提示。

## 🚀 快速开始

### 方式一：一键启动（推荐）⭐

#### Windows 用户

1. **首次使用 - 运行安装脚本：**
   ```bash
   双击 install.bat
   ```
   这会自动创建虚拟环境、安装依赖并创建配置文件

2. **编辑配置文件：**
   打开 `config/config.yaml`，填写你的 Bot Token 和 ID

3. **启动 Bot：**
   ```bash
   双击 start.bat
   ```

#### Linux/Mac 用户

1. **赋予脚本执行权限：**
   ```bash
   chmod +x start.sh install.sh update.sh
   ```

2. **运行安装脚本：**
   ```bash
   ./install.sh
   ```

3. **编辑配置文件：**
   ```bash
   nano config/config.yaml
   # 或使用你喜欢的编辑器
   ```

4. **启动 Bot：**
   ```bash
   ./start.sh
   ```

### 方式二：手动安装

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

#### 2. 配置 Bot

复制配置文件模板：

```bash
cp config/config.example.yaml config/config.yaml
```

编辑 `config/config.yaml`，填写以下信息：

```yaml
# Bot Token (从 Discord Developer Portal 获取)
token: "YOUR_BOT_TOKEN_HERE"

# 允许使用 bot 的身份组 ID 列表
allowed_role_ids:
  - 1234567890  # 替换为你的身份组 ID

# 频道管理配置
channel_manager:
  # 允许被修改名称的频道 ID 列表
  allowed_channel_ids:
    - 1234567890  # 替换为你要允许修改的频道 ID
```

#### 3. 获取必要的 ID

##### 获取身份组 ID：
1. 在 Discord 中右键点击身份组
2. 选择"复制 ID"（需要开启开发者模式）

##### 获取频道 ID：
1. 在 Discord 中右键点击频道
2. 选择"复制 ID"

##### 获取 Bot Token：
1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)
2. 创建或选择你的应用
3. 进入 "Bot" 页面
4. 点击 "Reset Token" 或 "Copy" 获取 Token

#### 4. 运行 Bot

```bash
python bot.py
```

## 📜 启动脚本说明

项目提供了多个便捷的启动脚本：

| 脚本 | 说明 | 适用系统 |
|------|------|----------|
| `start.bat` | 一键启动（自动检查环境和依赖） | Windows |
| `start.sh` | 一键启动（自动检查环境和依赖） | Linux/Mac |
| `install.bat` | 安装依赖和创建配置 | Windows |
| `install.sh` | 安装依赖和创建配置 | Linux/Mac |
| `update.bat` | 更新依赖包 | Windows |
| `update.sh` | 更新依赖包 | Linux/Mac |

### 脚本功能

所有启动脚本 (`start.bat` / `start.sh`) 都会自动：
1. ✅ 检查并创建虚拟环境
2. ✅ 激活虚拟环境
3. ✅ 检查并安装依赖包
4. ✅ 检查配置文件（首次运行会自动创建）
5. ✅ 启动 Bot

**优点：**
- 🔒 独立的虚拟环境，不污染系统 Python
- 🚀 一键启动，无需手动操作
- 🛡️ 自动检查依赖，避免运行错误

## 🔧 配置说明

### 主配置文件 (`config/config.yaml`)

```yaml
# Bot Token
token: "YOUR_BOT_TOKEN_HERE"

# 命令前缀
prefix: "/"

# 允许使用 bot 的身份组 ID 列表
allowed_role_ids:
  - 1234567890

# 频道管理配置
channel_manager:
  # 允许被修改名称的频道 ID 列表
  allowed_channel_ids:
    - 1234567890
  
  # 频道名称修改冷却时间（秒）
  cooldown: 300

# 一键冲水配置
message_cleaner:
  # 用户 Token（用于调用 Discord API v9 搜索接口）
  # 获取方法：F12 打开开发者工具 -> Network -> 找到任意请求 -> Headers -> Authorization
  user_token: "YOUR_USER_TOKEN_HERE"
  
  # 搜索间隔（秒）避免速率限制
  search_interval: 5
  
  # 删除批次大小（每批删除多少条消息）
  delete_batch_size: 10
  
  # 最大消息数（防止搜索过多消息）
  max_messages: 10000

# 热重载配置
hot_reload:
  enabled: true
  watch_interval: 2  # 配置文件检查间隔（秒）
```

## 📦 项目结构

```
Humanoid/
├── bot.py                      # 主入口文件
├── requirements.txt            # Python 依赖包
├── README.md                   # 项目文档
├── config/                     # 配置文件目录
│   ├── config.yaml            # 主配置文件
│   └── config.example.yaml    # 配置文件模板
├── cogs/                       # Cog 模块目录
│   ├── __init__.py
│   ├── channel_manager.py     # 频道管理模块
│   └── message_cleaner.py     # 一键冲水模块
└── utils/                      # 工具模块目录
    ├── __init__.py
    └── config_loader.py       # 配置加载器
```

## 🎮 使用说明

### 修改频道名称

1. 确保你拥有配置文件中指定的身份组
2. 在允许修改的频道中：
   - 输入 `/` 会自动显示可用命令
   - 选择 `/改改的名` 命令
   - 在 `新频道名` 参数中输入新名称
   - 按回车执行
3. Bot 会自动修改频道名称并显示结果

**提示：** 这是 Discord 原生斜杠命令，有自动完成和参数提示！

### 一键冲水功能

#### 获取用户 Token

在使用一键冲水功能前，需要先配置用户 Token：

1. 打开 Discord 网页版或客户端
2. 按 F12 打开开发者工具
3. 切换到 Network（网络）标签
4. 在 Discord 中进行任意操作（如发送消息）
5. 在网络请求中找到任意 Discord API 请求
6. 查看请求头（Headers）中的 `Authorization` 字段
7. 复制该值（不包括 "Authorization: " 前缀）
8. 填入 `config/config.yaml` 的 `message_cleaner.user_token` 配置项

**⚠️ 重要提示：**
- User Token 是敏感信息，请勿泄露！
- 不要将 Token 提交到公开的代码仓库
- 建议定期更换 Token

#### 使用方法

1. 确保你拥有配置文件中指定的身份组
2. 使用 `/一键冲水` 命令：
   ```
   /一键冲水 用户:@某用户
   ```
3. 可选参数：
   - `起始消息id`: 从指定消息开始搜索（默认从最新消息开始）
   - `频道`: 仅在指定频道搜索（默认搜索整个服务器）

#### 功能说明

- **搜索线程**: 每 5 秒调用一次 Discord API v9 搜索接口，查找目标用户的消息
- **删除线程**: 并行删除搜索到的消息
- **实时进度**: 通过 Embed 显示搜索/删除进度、剩余队列等信息
- **安全保护**: 自动跳过帖子首楼（message_id == thread_id），防止删除重要内容
- **可中断**: 使用 `/停止冲水` 命令随时停止任务

### 注意事项

#### 频道管理

- Discord API 限制：每个频道每 10 分钟最多修改 2 次名称
- 频道名称长度限制：1-100 个字符
- Bot 需要拥有 `管理频道` 权限才能修改频道名称

#### 一键冲水

- Bot 需要拥有 `管理消息` 权限才能删除消息
- 搜索使用用户 Token，可能受到速率限制
- 删除操作使用 Bot Token，也可能受到速率限制
- 建议在低峰时段使用，避免触发速率限制
- 帖子首楼会被自动跳过，不会被删除

## 🔥 热重载功能

### 配置文件热重载

Bot 会自动监控配置文件的变化，当检测到配置文件被修改时：
1. 自动重新加载配置
2. 通知所有模块更新配置
3. 无需重启 Bot

你可以直接编辑 `config/config.yaml` 文件，修改会在 2 秒内自动生效。

### 模块热重载

管理员可以使用 `/重载模块` 命令手动重载模块：

```
/重载模块
```

这将重新加载频道管理模块并自动同步命令，应用代码修改而无需重启 Bot。

## 🛠️ 开发指南

### 添加新的 Cog 模块

1. 在 `cogs/` 目录下创建新的 Python 文件
2. 创建继承自 `commands.Cog` 的类
3. 实现 `setup()` 函数
4. 在 `bot.py` 中添加模块路径到 `initial_extensions` 列表

示例：

```python
# cogs/my_module.py
import discord
from discord.ext import commands

class MyModule(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config_loader = bot.config_loader
    
    @commands.command(name='hello')
    async def hello(self, ctx):
        await ctx.send("Hello!")
    
    async def on_config_reload(self):
        """配置重载回调（可选）"""
        pass

async def setup(bot):
    await bot.add_cog(MyModule(bot))
```

## 📝 常见问题

### Q: Bot 无法启动？
A: 请检查：
- Token 是否正确配置
- 配置文件格式是否正确
- 是否已安装所有依赖包

### Q: 命令没有响应？
A: 请检查：
- 你的身份组是否在 `allowed_role_ids` 列表中
- 频道是否在 `allowed_channel_ids` 列表中
- Bot 是否拥有必要的权限

### Q: 无法修改频道名称？
A: 请检查：
- Bot 是否拥有 `管理频道` 权限
- 是否超过了 Discord API 的速率限制（每 10 分钟 2 次）

## 📄 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📮 联系方式

如有问题或建议，欢迎通过 Issue 联系。

---

Made with ❤️ for friends

