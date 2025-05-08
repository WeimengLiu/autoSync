#!/usr/bin/env python3
import os
import json
import time
import threading
from datetime import datetime
from pathlib import Path
import pyinotify
from sync_files import FileSyncHandler, setup_logger, sync_all_files, cleanup_empty_dirs

class TaskRunner:
    def __init__(self, task_id, name, input_dir, output_dir, extensions):
        self.task_id = task_id
        self.name = name
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.extensions = extensions
        self.status = "stopped"
        self.notifier = None
        self.handler = None
        self.thread = None
        self.logger = None
        self.start_time = None
        self.stop_time = None
        self._running = False
        self._lock = threading.Lock()

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "name": self.name,
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "extensions": self.extensions,
            "status": self.status,
            "start_time": self.start_time,
            "stop_time": self.stop_time
        }

    def _run_task(self):
        """在独立线程中运行任务"""
        try:
            # 设置日志
            self.logger = setup_logger(task_id=self.task_id, verbose=True)
            self.logger.info("任务 %s 启动", self.name)

            # 创建输出目录
            os.makedirs(self.output_dir, exist_ok=True)

            # 初始全量同步
            sync_all_files(self.input_dir, self.output_dir, self.extensions, self.logger)
            cleanup_empty_dirs(self.output_dir, self.logger)

            # 创建事件处理器
            wm = pyinotify.WatchManager()
            self.handler = FileSyncHandler(
                self.input_dir,
                self.output_dir,
                self.extensions,
                self.logger
            )

            # 创建通知器
            self.notifier = pyinotify.Notifier(wm, self.handler)
            
            # 添加监控，只监控指定的事件
            mask = (pyinotify.IN_CLOSE_WRITE | pyinotify.IN_MOVED_TO | 
                   pyinotify.IN_DELETE | pyinotify.IN_MOVED_FROM | 
                   pyinotify.IN_DELETE_SELF)
            self.logger.info("开始添加目录监控: %s", self.input_dir)
            wdd = wm.add_watch(self.input_dir, mask, rec=True, auto_add=True)
            
            # 检查监控是否成功添加
            if not wdd:
                self.logger.error("添加目录监控失败")
                return
            else:
                self.logger.info("成功添加监控的目录: %s", list(wdd.keys()))

            self.logger.info("开始监控目录变化...")
            self._running = True
            
            # 使用 check_events 和 read_events 的方式处理事件
            while self._running:
                try:
                    # 检查是否有新事件，设置1秒超时
                    if self.notifier.check_events(timeout=1000):
                        # 读取并处理所有待处理的事件
                        self.notifier.read_events()
                        self.notifier.process_events()
                except Exception as e:
                    self.logger.error("处理事件时出错: %s", str(e))

        except Exception as e:
            self.logger.error("任务运行出错: %s", str(e))
        finally:
            if self.notifier:
                self.notifier.stop()
            self._running = False
            with self._lock:
                self.status = "stopped"
                self.stop_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def start(self):
        with self._lock:
            if self.status == "running":
                print(f"任务 {self.name} 已经在运行中")
                return False

            try:
                print(f"开始启动任务 {self.name}")
                self.status = "running"
                self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.stop_time = None
                
                # 创建并启动新线程
                self.thread = threading.Thread(target=self._run_task)
                self.thread.daemon = True  # 设置为守护线程
                self.thread.start()
                
                # 等待线程真正启动
                time.sleep(1)
                
                print(f"任务 {self.name} 启动成功")
                return True
            except Exception as e:
                print(f"任务 {self.name} 启动失败: {str(e)}")
                self.status = "stopped"
                return False

    def stop(self):
        with self._lock:
            if self.status != "running":
                return False

            try:
                if self.logger:
                    self.logger.info("开始停止任务 %s", self.name)
                self._running = False  # 设置停止标志
                
                if self.notifier:
                    self.notifier.stop()
                
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=5)  # 等待线程结束
                    if self.thread.is_alive():
                        if self.logger:
                            self.logger.warning("任务 %s 线程仍在运行，强制终止", self.name)
                
                self.notifier = None
                self.handler = None
                self.thread = None

                self.status = "stopped"
                self.stop_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if self.logger:
                    self.logger.info("任务 %s 停止成功", self.name)
                return True
            except Exception as e:
                if self.logger:
                    self.logger.error("任务 %s 停止失败: %s", self.name, str(e))
                return False 