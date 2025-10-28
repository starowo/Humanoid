"""
配置加载器
支持配置文件热重载和自动更新
"""
import os
import yaml
import asyncio
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime


class ConfigLoader:
    """配置加载器，支持热重载"""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self._last_modified: Optional[float] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._callbacks = []
        
    def load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
            self._last_modified = os.path.getmtime(self.config_path)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 配置文件已加载")
            return self.config
        except yaml.YAMLError as e:
            raise ValueError(f"配置文件格式错误: {e}")
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项（支持点号分隔的嵌套键）"""
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        
        return value
    
    def reload_if_changed(self) -> bool:
        """如果配置文件已修改，则重新加载"""
        if not self.config_path.exists():
            return False
        
        current_mtime = os.path.getmtime(self.config_path)
        
        if self._last_modified is None or current_mtime > self._last_modified:
            try:
                old_config = self.config.copy()
                self.load_config()
                
                # 检查是否有实际变化
                if old_config != self.config:
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 检测到配置更新，已重新加载")
                    self._trigger_callbacks()
                    return True
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 配置重载失败: {e}")
                return False
        
        return False
    
    def add_reload_callback(self, callback):
        """添加配置重载回调函数"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
    
    def remove_reload_callback(self, callback):
        """移除配置重载回调函数"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    def _trigger_callbacks(self):
        """触发所有回调函数"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    asyncio.create_task(callback())
                else:
                    callback()
            except Exception as e:
                print(f"配置重载回调执行失败: {e}")
    
    async def start_watching(self, interval: int = 2):
        """启动配置文件监控"""
        if self._watch_task is not None:
            print("配置文件监控已在运行")
            return
        
        async def watch():
            while True:
                await asyncio.sleep(interval)
                self.reload_if_changed()
        
        self._watch_task = asyncio.create_task(watch())
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 配置文件监控已启动（间隔: {interval}秒）")
    
    def stop_watching(self):
        """停止配置文件监控"""
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 配置文件监控已停止")

