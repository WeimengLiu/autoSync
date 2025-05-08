#!/usr/bin/env python3
import os
import hashlib
import json
import mmap
import logging
import argparse
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
# 有条件地导入FSEventsObserver
import sys
if sys.platform == 'darwin':  # 仅在macOS上导入
    try:
        from watchdog.observers.fsevents import FSEventsObserver  # macOS 专用观察者
    except ImportError:
        FSEventsObserver = None
else:
    FSEventsObserver = None
    
from watchdog.events import (
    FileSystemEventHandler,
    FileClosedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileCreatedEvent,
    FileModifiedEvent
)
from multiprocessing import Pool, cpu_count, Manager
from functools import partial, lru_cache
import asyncio
import aiofiles
from aiofiles import os as aios
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from typing import Dict, Set, Deque
import time
import threading
from cache_manager import CacheManager

# 配置常量
DEFAULT_EXTENSIONS = "jpg,jpeg,png,gif,bmp,webp,ico,svg,nfo,srt,ass,ssa,sub,idx,smi,ssa,SRT,sup"
BATCH_SIZE = 100
BATCH_INTERVAL = 1.0
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10MB

class BatchProcessor:
    """批量处理器，用于优化文件处理性能"""
    def __init__(self, handler, batch_size: int = BATCH_SIZE, interval: float = BATCH_INTERVAL):
        self.handler = handler
        self.batch_size = batch_size
        self.interval = interval
        self.queue: Deque = deque()
        self.last_process_time = time.time()
        self.processing = False

    async def add_task(self, file_path: str, event_type: str) -> None:
        """添加任务到队列并按需处理"""
        self.queue.append((file_path, event_type))
        await self.process_batch_if_needed()

    async def process_batch_if_needed(self) -> None:
        """检查并处理批次任务"""
        current_time = time.time()
        if (len(self.queue) >= self.batch_size or 
            (self.queue and current_time - self.last_process_time >= self.interval)):
            await self.process_batch()

    async def process_batch(self) -> None:
        """处理一批任务"""
        if self.processing or not self.queue:
            return

        self.processing = True
        try:
            tasks = []
            while self.queue and len(tasks) < self.batch_size:
                file_path, event_type = self.queue.popleft()
                tasks.append(self.handler.sync_file_async(file_path, event_type))

            if tasks:
                await asyncio.gather(*tasks)
                self.last_process_time = time.time()
        finally:
            self.processing = False

class FileWriteMonitor:
    """文件写入监控器"""
    def __init__(self, file_path: str, logger: logging.Logger, check_interval: float = 0.5, stable_duration: float = 1.0):
        self.file_path = file_path
        self.logger = logger
        self.check_interval = check_interval  # 检查间隔（秒）
        self.stable_duration = stable_duration  # 文件需要保持稳定的时间（秒）
        self.last_size = 0
        self.last_mtime = 0
        self.last_stable_time = 0

    async def wait_for_completion(self) -> bool:
        """等待文件写入完成"""
        try:
            start_time = time.time()
            while True:
                if not os.path.exists(self.file_path):
                    self.logger.debug(f"文件不存在，等待创建: {self.file_path}")
                    await asyncio.sleep(self.check_interval)
                    continue

                current_size = os.path.getsize(self.file_path)
                current_mtime = os.path.getmtime(self.file_path)
                
                # 如果文件大小和修改时间都没变化
                if current_size == self.last_size and current_mtime == self.last_mtime:
                    # 如果这是第一次检测到稳定
                    if self.last_stable_time == 0:
                        self.last_stable_time = time.time()
                    # 如果文件已经保持稳定足够长时间
                    elif time.time() - self.last_stable_time >= self.stable_duration:
                        self.logger.debug(f"文件写入完成: {self.file_path} (大小: {current_size}字节)")
                        return True
                else:
                    # 如果文件发生变化，重置稳定时间
                    self.last_stable_time = 0
                    self.logger.debug(f"文件正在写入: {self.file_path} (大小: {current_size}字节)")

                self.last_size = current_size
                self.last_mtime = current_mtime
                
                # 如果等待时间过长，返回False
                if time.time() - start_time > 30:  # 最多等待30秒
                    self.logger.warning(f"等待文件写入完成超时: {self.file_path}")
                    return False
                    
                await asyncio.sleep(self.check_interval)
        except Exception as e:
            self.logger.error(f"监控文件写入失败: {self.file_path}, 错误: {e}")
            return False

class FileSyncHandler(FileSystemEventHandler):
    """文件同步处理器"""
    def __init__(self, input_dir: str, output_dir: str, extensions: str, logger: logging.Logger, task_id: str = None):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.extensions = extensions.split(',')
        self.logger = logger
        self.processing_files: Set[str] = set()
        self.event_loop = asyncio.new_event_loop()
        self.batch_processor = BatchProcessor(self)
        self.symlink_processed: Set[str] = set()  # 记录已处理的软链接
        self.write_monitors: Dict[str, asyncio.Task] = {}  # 文件写入监控任务
        
        # 初始化缓存管理器
        self.task_id = task_id or hashlib.md5(os.path.abspath(input_dir).encode()).hexdigest()
        self.cache_manager = CacheManager(self.task_id, logger)

    @lru_cache(maxsize=1000)
    def check_extension(self, file_path: str) -> bool:
        """检查文件扩展名（带缓存）"""
        return any(file_path.lower().endswith(ext.lower()) for ext in self.extensions)

    @lru_cache(maxsize=1000)
    def get_output_path(self, input_path: str) -> str:
        """获取输出路径（带缓存）"""
        return str(Path(self.output_dir) / Path(input_path).relative_to(self.input_dir))

    async def get_file_md5_async(self, file_path: str) -> str:
        """异步计算文件MD5（带缓存和mmap优化）"""
        try:
            mtime = os.path.getmtime(file_path)
            
            # 检查缓存
            cached_md5, cached_mtime = self.cache_manager.get_cache(file_path)
            if cached_md5 and cached_mtime == mtime:
                #self.logger.debug(f"使用缓存的MD5: {file_path}")
                return cached_md5

            self.logger.debug(f"计算文件MD5: {file_path}")
            file_size = os.path.getsize(file_path)
            md5_hash = hashlib.md5()

            if file_size >= LARGE_FILE_THRESHOLD:
                with open(file_path, 'rb') as f:
                    if file_size > 0:  # 确保文件不为空
                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                            md5_hash.update(mm)
                    else:  # 空文件特殊处理
                        self.logger.debug(f"空文件特殊处理: {file_path}")
                        pass  # 空文件的MD5就是空字符串的MD5
            else:
                async with aiofiles.open(file_path, 'rb') as f:
                    while chunk := await f.read(8192):
                        md5_hash.update(chunk)

            md5_value = md5_hash.hexdigest()
            
            # 更新缓存
            self.cache_manager.update_cache(file_path, md5_value, mtime)
            
            return md5_value
        except Exception as e:
            self.logger.error(f"计算MD5失败: {file_path}, 错误: {e}")
            return None

    async def sync_file_async(self, input_path: str, event_type: str) -> None:
        """异步同步单个文件"""
        if input_path in self.processing_files:
            return

        self.processing_files.add(input_path)
        try:
            if not Path(input_path).exists():
                return

            output_path = self.get_output_path(input_path)
            await aios.makedirs(os.path.dirname(output_path), exist_ok=True)

            if self.check_extension(input_path):
                # 处理需要复制的文件
                try:
                    # 获取源文件大小
                    source_size = os.path.getsize(input_path)
                    
                    # 如果是全量同步模式且目标文件已存在，只比较文件大小
                    if event_type == "initial" and Path(output_path).exists():
                        target_size = os.path.getsize(output_path)
                        if target_size == source_size:
                            # 文件大小相同，跳过MD5检查
                            return
                    else:
                        # 如果不是全量同步，或者文件不存在，则需要计算MD5
                        source_md5 = await self.get_file_md5_async(input_path)
                        if source_md5 is None:
                            raise Exception("无法计算源文件MD5")

                        # 检查是否需要更新
                        if Path(output_path).exists():
                            target_size = os.path.getsize(output_path)
                            if target_size == source_size:
                                target_md5 = await self.get_file_md5_async(output_path)
                                if target_md5 == source_md5:
                                    return

                    # 开始复制文件
                    temp_output_path = output_path + '.tmp'
                    try:
                        async with aiofiles.open(input_path, 'rb') as fsrc:
                            async with aiofiles.open(temp_output_path, 'wb') as fdst:
                                await fdst.write(await fsrc.read())

                        # 验证复制后的文件，不使用缓存
                        temp_size = os.path.getsize(temp_output_path)
                        # 直接计算临时文件的 MD5，不使用缓存
                        temp_md5_hash = hashlib.md5()
                        with open(temp_output_path, 'rb') as f:
                            if temp_size > 0:  # 确保文件不为空
                                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                                    temp_md5_hash.update(mm)
                            else:  # 空文件特殊处理
                                self.logger.debug(f"空文件特殊处理: {temp_output_path}")
                        temp_md5 = temp_md5_hash.hexdigest()
                        
                        if temp_size != source_size or temp_md5 != source_md5:
                            raise Exception("文件复制验证失败")

                        if os.path.exists(output_path):
                            os.remove(output_path)
                        os.rename(temp_output_path, output_path)
                        # 更新目标文件的MD5缓存
                        self.cache_manager.update_cache(output_path, temp_md5, os.path.getmtime(output_path))
                        # 只在复制完成后输出一次日志
                        if event_type == "write_complete":
                            self.logger.info(f"[完成] {Path(input_path).relative_to(self.input_dir)}")
                        else:
                            self.logger.info(f"[复制] {Path(input_path).relative_to(self.input_dir)}")

                    except Exception as e:
                        if os.path.exists(temp_output_path):
                            os.remove(temp_output_path)
                        raise e

                except Exception as e:
                    self.logger.error(f"[错误] {Path(input_path).relative_to(self.input_dir)}: {e}")

            else:
                # 处理需要创建软链接的文件
                if os.path.exists(output_path):
                    if os.path.islink(output_path):
                        try:
                            if os.path.samefile(os.path.realpath(output_path), input_path):
                                return
                        except OSError:
                            pass
                    os.remove(output_path)
                
                os.symlink(input_path, output_path)
                self.logger.info(f"[链接] {Path(input_path).relative_to(self.input_dir)}")

        except Exception as e:
            self.logger.error(f"[错误] {Path(input_path).relative_to(self.input_dir)}: {e}")
        finally:
            self.processing_files.remove(input_path)

    def dispatch(self, event):
        """重写 dispatch 方法以处理文件系统事件"""
        # 检查路径是否在监控目录内
        if hasattr(event, 'dest_path'):  # 移动事件
            paths = [event.src_path, event.dest_path]
        else:
            paths = [event.src_path]
        
        valid_paths = [p for p in paths if p.startswith(self.input_dir)]
        if not valid_paths or event.is_directory:
            return

        try:
            # 处理删除事件
            if isinstance(event, FileDeletedEvent):
                self._cancel_write_monitor(event.src_path)
                self.on_deleted(event)
                self.symlink_processed.discard(event.src_path)
                return

            # 处理复制文件的创建/修改事件
            if self.check_extension(event.src_path):
                if isinstance(event, (FileCreatedEvent, FileModifiedEvent)):
                    self._handle_file_change(event.src_path)
            # 处理软链接文件
            elif isinstance(event, FileCreatedEvent):
                if event.src_path not in self.symlink_processed:
                    self.on_symlink_update(event)
                    self.symlink_processed.add(event.src_path)
            
            # 处理移动事件
            if isinstance(event, FileMovedEvent):
                self._cancel_write_monitor(event.src_path)
                self.on_moved(event)
                self.symlink_processed.discard(event.src_path)
                if not self.check_extension(event.dest_path):
                    self.symlink_processed.add(event.dest_path)
                self.logger.info(f"[移动] {Path(event.src_path).relative_to(self.input_dir)} -> {Path(event.dest_path).relative_to(self.input_dir)}")

        except Exception as e:
            self.logger.error(f"[错误] {Path(event.src_path).relative_to(self.input_dir)}: {e}")

    def _handle_file_change(self, file_path: str):
        """处理文件变化事件"""
        # 如果已经有监控任务在运行，先取消它
        if file_path in self.write_monitors:
            self._cancel_write_monitor(file_path)
        
        # 创建新的监控任务
        async def monitor_and_sync():
            monitor = FileWriteMonitor(file_path, self.logger)
            if await monitor.wait_for_completion():
                await self.batch_processor.add_task(file_path, "write_complete")
            else:
                self.logger.warning(f"[超时] {Path(file_path).relative_to(self.input_dir)}")

        task = asyncio.run_coroutine_threadsafe(monitor_and_sync(), self.event_loop)
        self.write_monitors[file_path] = task

    def _cancel_write_monitor(self, file_path: str):
        """取消文件的写入监控任务"""
        if file_path in self.write_monitors:
            task = self.write_monitors.pop(file_path)
            task.cancel()

    def on_closed(self, event):
        """处理需要复制的文件的写入完成事件"""
        if not Path(event.src_path).exists():
            return
        self._run_async_task(self.batch_processor.add_task(event.src_path, "write_complete"))

    def on_symlink_update(self, event):
        """处理需要创建软链接的文件的创建事件"""
        if not Path(event.src_path).exists():
            return
            
        output_path = self.get_output_path(event.src_path)
        try:
            # 如果目标路径已存在
            if os.path.exists(output_path):
                if os.path.islink(output_path):
                    try:
                        if os.path.samefile(os.path.realpath(output_path), event.src_path):
                            return
                    except OSError:
                        pass
                os.remove(output_path)
            
            # 创建父目录
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            # 创建软链接
            os.symlink(event.src_path, output_path)
            # 移到这里输出日志，确保软链接创建成功后才输出
            self.logger.info(f"[链接] {Path(event.src_path).relative_to(self.input_dir)}")
        except Exception as e:
            self.logger.error(f"[错误] 创建软链接失败 {Path(event.src_path).relative_to(self.input_dir)}: {e}")
            self.symlink_processed.discard(event.src_path)

    def on_deleted(self, event):
        """处理文件删除事件"""
        try:
            output_path = self.get_output_path(event.src_path)
            self.logger.debug(f"检测到删除事件，源文件: {event.src_path}")
            self.logger.debug(f"对应的输出路径: {output_path}")
            
            if os.path.lexists(output_path):  # 使用lexists来检查软链接本身是否存在
                try:
                    if os.path.islink(output_path):
                        self.logger.debug(f"删除软链接: {output_path}")
                    else:
                        self.logger.debug(f"删除普通文件: {output_path}")
                    os.remove(output_path)
                    self.logger.info(f"已删除: {Path(event.src_path).relative_to(self.input_dir)}")
                except Exception as e:
                    self.logger.error(f"删除文件失败 {output_path}: {e}")
            else:
                self.logger.debug(f"输出路径不存在，无需删除: {output_path}")
            
            # 清理空目录
            output_dir = os.path.dirname(output_path)
            try:
                while output_dir and output_dir.startswith(self.output_dir):
                    if not os.listdir(output_dir):
                        os.rmdir(output_dir)
                        self.logger.debug(f"删除空目录: {output_dir}")
                        output_dir = os.path.dirname(output_dir)
                    else:
                        break
            except Exception as e:
                self.logger.debug(f"清理空目录失败: {e}")
        except Exception as e:
            self.logger.error(f"处理删除事件失败: {e}")

    def on_moved(self, event):
        """处理文件移动事件"""
        self.on_deleted(event)  # 处理源文件的删除
        if Path(event.dest_path).exists():
            if self.check_extension(event.dest_path):
                # 对于需要复制的文件，等待文件写入完成
                self._run_async_task(self.batch_processor.add_task(event.dest_path, "moved"))
            else:
                # 对于需要创建软链接的文件，立即处理
                self.on_symlink_update(event)

    def _run_async_task(self, coro):
        """在事件循环中运行异步任务"""
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.event_loop)
            future.result()  # 等待任务完成
        except Exception as e:
            self.logger.error(f"运行异步任务失败: {e}")

    def __del__(self):
        """清理资源"""
        # 取消所有监控任务
        for task in self.write_monitors.values():
            task.cancel()
        self.write_monitors.clear()
        
        # 关闭事件循环
        if hasattr(self, 'event_loop') and self.event_loop.is_running():
            self.event_loop.stop()
            self.event_loop.close()

async def check_and_handle_existing_symlinks(output_dir: str, input_dir: str, logger: logging.Logger) -> None:
    """检查并处理现有的软链接"""
    logger.info("[开始] 检查现有软链接")
    invalid_count = 0
    valid_count = 0
    
    for root, _, files in os.walk(output_dir):
        for file in files:
            output_path = os.path.join(root, file)
            if os.path.islink(output_path):
                try:
                    # 获取软链接的目标路径
                    target_path = os.path.realpath(output_path)
                    # 检查软链接是否有效
                    if not os.path.exists(target_path) or not target_path.startswith(input_dir):
                        os.remove(output_path)
                        invalid_count += 1
                        logger.info(f"[清理] 无效链接: {Path(output_path).relative_to(output_dir)} -> {target_path}")
                    else:
                        valid_count += 1
                except OSError as e:
                    try:
                        os.remove(output_path)
                        invalid_count += 1
                        logger.info(f"[清理] 损坏链接: {Path(output_path).relative_to(output_dir)}")
                    except OSError as e2:
                        logger.error(f"[错误] 无法删除损坏链接 {Path(output_path).relative_to(output_dir)}: {e2}")

    logger.info(f"[完成] 链接检查: 有效 {valid_count} 个, 清理 {invalid_count} 个")

async def sync_all_files(input_dir: str, output_dir: str, extensions: str, logger: logging.Logger, task_id: str = None) -> None:
    handler = FileSyncHandler(input_dir, output_dir, extensions, logger, task_id)
    logger.info(f"[开始] 全量同步: {input_dir} -> {output_dir}")
    
    # 收集所有输出目录中的软链接
    output_symlinks = set()
    for root, _, files in os.walk(output_dir):
        for file in files:
            output_path = os.path.join(root, file)
            if os.path.islink(output_path):
                output_symlinks.add(output_path)
    
    # 统计需要处理的文件数量
    total_files = sum(len(files) for _, _, files in os.walk(input_dir))
    processed_paths = set()
    processed_files = 0
    
    # 设置进度报告的间隔（约5%）
    progress_interval = max(1, int(total_files * 0.05))
    last_reported_progress = 0
    
    # 同步文件并处理软链接
    for root, _, files in os.walk(input_dir):
        for file in files:
            input_path = os.path.join(root, file)
            output_path = handler.get_output_path(input_path)
            processed_paths.add(output_path)
            
            try:
                await handler.sync_file_async(input_path, "initial")
                processed_files += 1
                
                # 使用百分比间隔显示进度
                current_progress = processed_files // progress_interval
                if current_progress > last_reported_progress:
                    progress_percentage = (processed_files / total_files) * 100
                    logger.info(f"[进度] {processed_files}/{total_files} ({progress_percentage:.1f}%)")
                    last_reported_progress = current_progress
            except Exception as e:
                logger.error(f"[错误] {Path(input_path).relative_to(input_dir)}: {e}")
    
    logger.info(f"[完成] 全量同步，共处理 {processed_files} 个文件")

def cleanup_empty_dirs(directory: str, logger: logging.Logger) -> None:
    """清理空目录"""
    for root, dirs, files in os.walk(directory, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            try:
                if not os.listdir(dir_path):  # 检查目录是否为空
                    os.rmdir(dir_path)
                    logger.info(f"已删除空目录: {dir_path}")
            except Exception as e:
                logger.error(f"删除空目录失败 {dir_path}: {e}")

def setup_logger(verbose: bool = False, task_id: str = None) -> logging.Logger:
    """配置日志记录器"""
    logger_name = f'FileSync_{task_id}' if task_id else 'FileSync'
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # 总是设置为DEBUG级别以捕获所有日志

    if not logger.handlers:
        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)  # 控制台只显示INFO及以上级别
        console_formatter = logging.Formatter('%(message)s')  # 控制台使用简单格式
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # 文件处理器
        log_dir = Path(__file__).parent / 'logs'
        log_dir.mkdir(exist_ok=True)
        
        # 使用年月日作为日志文件名
        if task_id:
            file_name = f'file_sync_{task_id}_{datetime.now():%Y%m%d}.log'
        else:
            file_name = f'file_sync_{datetime.now():%Y%m%d}.log'
            
        file_handler = logging.FileHandler(
            log_dir / file_name,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)  # 文件记录所有级别的日志
        
        # 文件日志使用详细格式
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - [%(name)s] - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # 如果是调试模式，控制台也显示DEBUG级别的日志
        if verbose:
            console_handler.setFormatter(file_formatter)  # 使用详细格式

    return logger

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='文件自动同步工具')
    parser.add_argument('input_dir', help='输入目录')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('--extensions', default=DEFAULT_EXTENSIONS, help='要同步的文件后缀，用逗号分隔')
    parser.add_argument('--verbose', action='store_true', help='显示详细日志')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='批处理大小')
    parser.add_argument('--batch-interval', type=float, default=BATCH_INTERVAL, help='批处理间隔（秒）')
    args = parser.parse_args()

    logger = setup_logger(args.verbose)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # 执行初始同步
    logger.info("开始全量同步...")
    asyncio.run(sync_all_files(args.input_dir, args.output_dir, args.extensions, logger))
    logger.info("全量同步完成")

    # 启动文件监控，使用FSEventsObserver来支持文件关闭事件
    if sys.platform == 'darwin' and FSEventsObserver is not None:  # macOS且成功导入
        observer = FSEventsObserver()
        logger.info("使用macOS原生FSEventsObserver")
    else:  # 其他平台或导入失败
        observer = Observer()
        logger.warning("使用标准Observer，不支持文件关闭事件监听，将使用修改事件代替")

    event_handler = FileSyncHandler(args.input_dir, args.output_dir, args.extensions, logger)
    observer.schedule(event_handler, args.input_dir, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == '__main__':
    main() 