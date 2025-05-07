#!/usr/bin/env python3
import os
import sys
import shutil
import argparse
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

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

class FileSyncHandler(FileSystemEventHandler):
    def __init__(self, input_dir, output_dir, extensions, logger):
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.extensions = extensions.split(',')
        self.logger = logger
        self.processing_files = set()  # 用于跟踪正在处理的文件

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
        """计算文件的MD5哈希值"""
        try:
            md5_hash = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    md5_hash.update(chunk)
            return md5_hash.hexdigest()
        except Exception:
            return None

    def files_are_identical(self, file1, file2):
        """检查两个文件是否完全相同"""
        try:
            # 首先比较文件大小
            if os.path.getsize(file1) != os.path.getsize(file2):
                return False
            
            # 如果大小相同，比较MD5哈希值
            md5_1 = self.get_file_md5(file1)
            md5_2 = self.get_file_md5(file2)
            return md5_1 is not None and md5_2 is not None and md5_1 == md5_2
        except Exception:
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
                        self.logger.info("文件已存在且内容相同: %s -> %s", input_path, output_path)
                        return
                self.logger.info("复制文件: %s -> %s", input_path, output_path)
                shutil.copy2(input_path, output_path)
            else:
                # 创建软链接
                if os.path.exists(output_path):
                    if self.is_valid_symlink(output_path, input_path):
                        self.logger.info("软链接已存在且正确: %s -> %s", input_path, output_path)
                        return
                    else:
                        os.remove(output_path)
                self.logger.info("创建软链接: %s -> %s", input_path, output_path)
                os.symlink(input_path, output_path)
        except Exception as e:
            self.logger.error("同步文件失败: %s -> %s, 错误: %s", input_path, output_path, str(e))
        finally:
            self.processing_files.remove(input_path)

    def on_any_event(self, event):
        """统一处理文件变化事件"""
        if event.is_directory:
            return

        if event.event_type in ['created', 'modified']:
            # 如果是新创建的文件，忽略修改事件
            if event.src_path in self.processing_files:
                return
            self.logger.info("检测到文件变化[%s]: %s", event.event_type, event.src_path)
            self.sync_file(event.src_path, event.event_type)
        elif event.event_type == 'deleted':
            output_path = self.get_output_path(event.src_path)
            if os.path.exists(output_path):
                self.logger.info("删除文件: %s", output_path)
                os.remove(output_path)
        elif event.event_type == 'moved':
            self.logger.info("检测到文件移动: %s -> %s", event.src_path, event.dest_path)
            # 删除旧文件
            old_output_path = self.get_output_path(event.src_path)
            if os.path.exists(old_output_path):
                os.remove(old_output_path)
            # 同步新文件
            self.sync_file(event.dest_path, "MOVE")

def sync_all_files(input_dir, output_dir, extensions, logger):
    """全量同步所有文件"""
    logger.info("开始全量同步...")

    # 创建一个FileSyncHandler实例
    handler = FileSyncHandler(input_dir, output_dir, extensions, logger)

    for root, _, files in os.walk(input_dir):
        for file in files:
            input_path = os.path.join(root, file)
            handler.sync_file(input_path, "INIT")

def cleanup_empty_dirs(output_dir, logger):
    """清理空目录"""
    for root, dirs, _ in os.walk(output_dir, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            if not os.listdir(dir_path):
                logger.info("删除空目录: %s", dir_path)
                os.rmdir(dir_path)

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
    event_handler = FileSyncHandler(args.input_dir, args.output_dir, args.extensions, logger)
    observer = Observer()
    observer.schedule(event_handler, args.input_dir, recursive=True)
    observer.start()

    logger.info("开始监控目录变化...")

    try:
        while True:
            observer.join(1)
    except KeyboardInterrupt:
        observer.stop()
        logger.info("停止监控")
    observer.join()

if __name__ == "__main__":
    main() 