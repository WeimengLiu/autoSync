#!/usr/bin/env python3
import os
import sys
import shutil
import argparse
import logging
import hashlib
import json
from datetime import datetime
from pathlib import Path
import pyinotify
from multiprocessing import Pool, cpu_count
from functools import partial

# 默认文件后缀列表
DEFAULT_EXTENSIONS = "jpg,jpeg,png,gif,bmp,webp,ico,svg,nfo,srt,ass,ssa,sub,idx,smi,ssa,SRT,sup"

# 配置日志
def setup_logger(task_id=None, verbose=False):
    """设置日志记录器"""
    # 为每个任务创建独立的logger
    logger_name = f'FileSync_{task_id}' if task_id else 'FileSync'
    logger = logging.getLogger(logger_name)
    
    # 如果logger已经有handler，直接返回
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    # 创建文件处理器
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    # 为每个任务创建独立的日志文件
    if task_id:
        log_file = os.path.join(log_dir, f'file_sync_{task_id}_{datetime.now().strftime("%Y%m%d")}.log')
    else:
        log_file = os.path.join(log_dir, f'file_sync_{datetime.now().strftime("%Y%m%d")}.log')
        
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    
    # 设置日志格式
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    # 添加处理器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

class FileSyncHandler(pyinotify.ProcessEvent):
    def __init__(self, input_dir, output_dir, extensions, logger):
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.extensions = extensions.split(',')
        self.logger = logger
        self.processing_files = set()  # 用于跟踪正在处理的文件
        self.md5_cache = {}  # 缓存文件的MD5值
        self.mtime_cache = {}  # 缓存文件的修改时间
        self.cache_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'cache',
            f'sync_cache_{hashlib.md5(input_dir.encode()).hexdigest()}.json'
        )
        self.load_cache()
        self.logger.info("初始化 FileSyncHandler，监控目录: %s", input_dir)

    def load_cache(self):
        """从磁盘加载缓存"""
        try:
            # 确保缓存目录存在
            cache_dir = os.path.dirname(self.cache_file)
            os.makedirs(cache_dir, exist_ok=True)
            
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                    self.md5_cache = cache_data.get('md5_cache', {})
                    self.mtime_cache = cache_data.get('mtime_cache', {})
                self.logger.info("已加载缓存文件: %s", self.cache_file)
                self.logger.debug("缓存包含 %d 个文件记录", len(self.md5_cache))
            else:
                self.logger.info("缓存文件不存在，将创建新缓存: %s", self.cache_file)
        except Exception as e:
            self.logger.error("加载缓存失败: %s", str(e))
            self.md5_cache = {}
            self.mtime_cache = {}

    def save_cache(self):
        """将缓存保存到磁盘"""
        try:
            # 确保缓存目录存在
            cache_dir = os.path.dirname(self.cache_file)
            os.makedirs(cache_dir, exist_ok=True)
            
            # 保存缓存数据
            cache_data = {
                'md5_cache': self.md5_cache,
                'mtime_cache': self.mtime_cache,
                'last_update': datetime.now().isoformat()
            }
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            self.logger.debug("缓存已保存到: %s", self.cache_file)
        except Exception as e:
            self.logger.error("保存缓存失败: %s", str(e))

    def clear_cache(self):
        """清理缓存"""
        self.md5_cache.clear()
        self.mtime_cache.clear()
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
                self.logger.info("缓存文件已删除: %s", self.cache_file)
        except Exception as e:
            self.logger.error("删除缓存文件失败: %s", str(e))

    def check_extension(self, file_path):
        """检查文件是否在需要复制的后缀列表中"""
        return any(file_path.lower().endswith(ext.lower()) for ext in self.extensions)

    def get_output_path(self, input_path):
        """获取输出文件路径"""
        rel_path = os.path.relpath(input_path, self.input_dir)
        return os.path.join(self.output_dir, rel_path)

    def is_valid_symlink(self, link_path, target_path):
        """检查软链接是否有效且指向正确的目标"""
        try:
            return os.path.islink(link_path) and os.path.realpath(link_path) == os.path.realpath(target_path)
        except Exception:
            return False

    def get_file_md5(self, file_path):
        """计算文件的MD5哈希值，使用缓存机制"""
        try:
            # 获取文件的修改时间
            mtime = os.path.getmtime(file_path)
            
            # 如果文件在缓存中且修改时间未变，直接返回缓存的MD5值
            if file_path in self.md5_cache and self.mtime_cache.get(file_path) == mtime:
                return self.md5_cache[file_path]
            
            # 计算新的MD5值
            md5_hash = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    md5_hash.update(chunk)
            md5_value = md5_hash.hexdigest()
            
            # 更新缓存
            self.md5_cache[file_path] = md5_value
            self.mtime_cache[file_path] = mtime
            
            # 定期保存缓存（这里设置为每更新10个文件保存一次）
            if len(self.md5_cache) % 10 == 0:
                self.save_cache()
            
            return md5_value
        except Exception as e:
            self.logger.error("计算MD5失败: %s, 错误: %s", file_path, str(e))
            return None

    def files_are_identical(self, file1, file2):
        """检查两个文件是否完全相同，使用缓存机制"""
        try:
            # 首先比较文件大小
            if os.path.getsize(file1) != os.path.getsize(file2):
                return False
            
            # 获取两个文件的修改时间
            mtime1 = os.path.getmtime(file1)
            mtime2 = os.path.getmtime(file2)
            
            # 如果两个文件都在缓存中且修改时间未变，直接比较缓存的MD5值
            if (file1 in self.md5_cache and self.mtime_cache.get(file1) == mtime1 and
                file2 in self.md5_cache and self.mtime_cache.get(file2) == mtime2):
                self.logger.debug("使用缓存比较文件: %s 和 %s", file1, file2)
                return self.md5_cache[file1] == self.md5_cache[file2]
            
            # 计算并比较MD5值
            md5_1 = self.get_file_md5(file1)
            md5_2 = self.get_file_md5(file2)
            return md5_1 is not None and md5_2 is not None and md5_1 == md5_2
        except Exception as e:
            self.logger.error("比较文件失败: %s 和 %s, 错误: %s", file1, file2, str(e))
            return False

    def sync_file(self, input_path, event_type):
        """同步单个文件"""
        if not os.path.isfile(input_path):
            return

        # 如果文件正在处理中，跳过
        if input_path in self.processing_files:
            return

        try:
            self.processing_files.add(input_path)
            output_path = self.get_output_path(input_path)
            output_dir = os.path.dirname(output_path)

            # 创建输出目录
            os.makedirs(output_dir, exist_ok=True)

            if self.check_extension(input_path):
                # 复制文件
                if os.path.exists(output_path):
                    if self.files_are_identical(input_path, output_path):
                        return
                self.logger.info("复制文件: %s", os.path.relpath(input_path, self.input_dir))
                shutil.copy2(input_path, output_path)
            else:
                # 创建软链接
                if os.path.exists(output_path):
                    if self.is_valid_symlink(output_path, input_path):
                        return
                    else:
                        os.remove(output_path)
                self.logger.info("创建软链接: %s", os.path.relpath(input_path, self.input_dir))
                os.symlink(input_path, output_path)
        except Exception as e:
            self.logger.error("同步文件失败: %s, 错误: %s", os.path.relpath(input_path, self.input_dir), str(e))
        finally:
            self.processing_files.remove(input_path)

    def process_IN_CLOSE_WRITE(self, event):
        """处理文件写入完成事件"""
        if not event.dir:
            self.sync_file(event.pathname, "CLOSE_WRITE")

    def process_IN_MOVED_TO(self, event):
        """处理文件移动到事件"""
        if not event.dir:
            self.sync_file(event.pathname, "MOVED_TO")

    def process_IN_DELETE(self, event):
        """处理文件删除事件"""
        if event.dir:
            self.logger.info("删除目录: %s", os.path.relpath(event.pathname, self.input_dir))
            self._handle_dir_delete(event.pathname)
        else:
            self.logger.info("删除文件: %s", os.path.relpath(event.pathname, self.input_dir))
            self._handle_delete(event.pathname)

    def process_IN_MOVED_FROM(self, event):
        """处理文件移出事件（相当于删除）"""
        if event.dir:
            self.logger.info("删除目录(移出): %s", os.path.relpath(event.pathname, self.input_dir))
            self._handle_dir_delete(event.pathname)
        else:
            self.logger.info("删除文件(移出): %s", os.path.relpath(event.pathname, self.input_dir))
            self._handle_delete(event.pathname)

    def process_IN_DELETE_SELF(self, event):
        """处理文件自身被删除事件"""
        if event.dir:
            self.logger.info("删除目录(自身): %s", os.path.relpath(event.pathname, self.input_dir))
            self._handle_dir_delete(event.pathname)
        else:
            self.logger.info("删除文件(自身): %s", os.path.relpath(event.pathname, self.input_dir))
            self._handle_delete(event.pathname)

    def _handle_dir_delete(self, pathname):
        """统一处理目录删除逻辑"""
        output_path = self.get_output_path(pathname)
        
        if os.path.exists(output_path):
            try:
                shutil.rmtree(output_path)
            except Exception as e:
                self.logger.error("删除目录失败: %s, 错误: %s", os.path.relpath(pathname, self.input_dir), str(e))

    def _handle_delete(self, pathname):
        """统一处理文件删除逻辑"""
        output_path = self.get_output_path(pathname)
        
        if os.path.lexists(output_path):
            try:
                os.remove(output_path)
            except Exception as e:
                self.logger.error("删除文件失败: %s, 错误: %s", os.path.relpath(pathname, self.input_dir), str(e))

    def process_default(self, event):
        """处理其他类型的事件"""
        pass

def sync_single_file(handler, file_info):
    """同步单个文件（用于多进程处理）"""
    input_path, rel_path = file_info
    try:
        # 获取输出文件路径
        output_path = os.path.join(handler.output_dir, rel_path)
        
        # 如果输出文件存在且修改时间相同，跳过同步
        if (os.path.exists(output_path) and 
            os.path.getmtime(output_path) == os.path.getmtime(input_path) and
            os.path.getsize(input_path) == os.path.getsize(output_path)):
            return {'status': 'skipped', 'file': rel_path}
        
        # 创建输出目录
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        if handler.check_extension(input_path):
            # 复制文件
            shutil.copy2(input_path, output_path)
        else:
            # 创建软链接
            if os.path.exists(output_path):
                os.remove(output_path)
            os.symlink(input_path, output_path)
        
        return {'status': 'synced', 'file': rel_path}
    except Exception as e:
        return {'status': 'error', 'file': rel_path, 'error': str(e)}

def sync_all_files(input_dir, output_dir, extensions, logger):
    """全量同步所有文件（使用多进程）"""
    logger.info("开始全量同步...")

    # 创建FileSyncHandler实例
    handler = FileSyncHandler(input_dir, output_dir, extensions, logger)

    try:
        # 收集需要同步的文件
        files_to_sync = []
        source_files = set()
        
        for root, _, files in os.walk(input_dir):
            for file in files:
                input_path = os.path.join(root, file)
                rel_path = os.path.relpath(input_path, input_dir)
                source_files.add(rel_path)
                files_to_sync.append((input_path, rel_path))

        # 确定进程数（使用CPU核心数的2倍，但不超过文件数）
        num_processes = min(cpu_count() * 2, len(files_to_sync))
        logger.info("使用 %d 个进程进行并行同步", num_processes)

        # 创建进程池
        with Pool(num_processes) as pool:
            # 使用partial固定handler参数
            sync_func = partial(sync_single_file, handler)
            
            # 使用进程池并行处理文件
            total_files = len(files_to_sync)
            synced_files = 0
            skipped_files = 0
            error_files = 0
            
            # 使用imap_unordered获取实时结果
            for result in pool.imap_unordered(sync_func, files_to_sync):
                if result['status'] == 'synced':
                    synced_files += 1
                elif result['status'] == 'skipped':
                    skipped_files += 1
                else:
                    error_files += 1
                    logger.error("同步失败: %s, 错误: %s", result['file'], result['error'])

                # 显示进度
                if (synced_files + skipped_files) % 100 == 0 or (synced_files + skipped_files) == total_files:
                    progress = (synced_files + skipped_files) / total_files * 100
                    logger.info("同步进度: %.1f%% (%d/%d)", progress, synced_files + skipped_files, total_files)

        # 检查并删除不存在的文件
        removed_files = 0
        for root, _, files in os.walk(output_dir):
            for file in files:
                output_path = os.path.join(root, file)
                rel_path = os.path.relpath(output_path, output_dir)
                
                if rel_path not in source_files:
                    try:
                        os.remove(output_path)
                        removed_files += 1
                    except Exception as e:
                        logger.error("删除文件失败: %s, 错误: %s", rel_path, str(e))

        # 同步完成后保存缓存
        handler.save_cache()
        
        # 输出同步统计信息
        logger.info("\n同步完成:")
        logger.info("- 总文件数: %d", total_files)
        logger.info("- 已同步: %d", synced_files)
        logger.info("- 已跳过: %d", skipped_files)
        logger.info("- 已删除: %d", removed_files)
        if error_files > 0:
            logger.error("- 同步失败: %d", error_files)

    except Exception as e:
        logger.error("同步过程出错: %s", str(e))
        handler.save_cache()
        raise

def cleanup_empty_dirs(output_dir, logger):
    """清理空目录"""
    for root, dirs, _ in os.walk(output_dir, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            if not os.listdir(dir_path):
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    logger.error("删除空目录失败: %s, 错误: %s", dir_path, str(e))

def main():
    parser = argparse.ArgumentParser(description='文件同步工具')
    parser.add_argument('input_dir', help='输入目录')
    parser.add_argument('output_dir', help='输出目录')
    parser.add_argument('-e', '--extensions', default=DEFAULT_EXTENSIONS,
                      help=f'文件后缀名（用逗号分隔，默认: {DEFAULT_EXTENSIONS}）')
    parser.add_argument('-v', '--verbose', action='store_true',
                      help='显示详细输出')
    args = parser.parse_args()

    # 设置日志记录器
    logger = setup_logger(args.verbose)

    # 检查输入目录是否存在
    if not os.path.isdir(args.input_dir):
        logger.error("错误: 输入目录 '%s' 不存在", args.input_dir)
        sys.exit(1)

    # 创建输出目录
    os.makedirs(args.output_dir, exist_ok=True)

    logger.info("配置信息:")
    logger.info("输入目录: %s", args.input_dir)
    logger.info("输出目录: %s", args.output_dir)
    logger.info("文件后缀: %s", args.extensions)
    logger.info("详细模式: %s", "开启" if args.verbose else "关闭")
    logger.info("")

    # 初始全量同步
    sync_all_files(args.input_dir, args.output_dir, args.extensions, logger)
    cleanup_empty_dirs(args.output_dir, logger)

    # 设置文件监控
    wm = pyinotify.WatchManager()
    handler = FileSyncHandler(args.input_dir, args.output_dir, args.extensions, logger)
    notifier = pyinotify.Notifier(wm, handler)
    
    # 添加监控，只监控指定的事件
    mask = pyinotify.IN_CLOSE_WRITE | pyinotify.IN_MOVED_TO | pyinotify.IN_DELETE
    wm.add_watch(args.input_dir, mask, rec=True, auto_add=True)

    logger.info("开始监控目录变化...")

    try:
        notifier.loop()
    except KeyboardInterrupt:
        notifier.stop()
        logger.info("停止监控")

if __name__ == '__main__':
    main() 