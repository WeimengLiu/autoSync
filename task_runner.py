#!/usr/bin/env python3
import os
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from watchdog.observers import Observer
from sync_files import FileSyncHandler, setup_logger, sync_all_files, cleanup_empty_dirs

class TaskRunner:
    def __init__(self, task_id, name, input_dir, output_dir, extensions):
        self.task_id = task_id
        self.name = name
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.extensions = extensions
        self.status = "stopped"
        self.observer = None
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
            self.handler = FileSyncHandler(
                self.input_dir,
                self.output_dir,
                self.extensions,
                self.logger
            )

            # 创建观察者
            self.observer = Observer()
            self.observer.schedule(self.handler, self.input_dir, recursive=True)
            self.observer.start()

            self._running = True
            while self._running:
                time.sleep(0.1)  # 避免CPU占用过高

        except Exception as e:
            self.logger.error("任务运行出错: %s", str(e))
        finally:
            if self.observer:
                self.observer.stop()
                self.observer.join()
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
                time.sleep(0.1)
                
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
                
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=5)  # 等待线程结束
                    if self.thread.is_alive():
                        if self.logger:
                            self.logger.warning("任务 %s 线程仍在运行，强制终止", self.name)
                
                if self.observer:
                    self.observer.stop()
                    self.observer.join(timeout=1)
                
                self.observer = None
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