#!/usr/bin/env python3
import os
import json
import time
from datetime import datetime
from task_runner import TaskRunner

class TaskManager:
    def __init__(self, config_file="tasks.json"):
        self.config_file = config_file
        self.tasks = {}
        self.load_tasks()

    def load_tasks(self):
        """从配置文件加载任务"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    tasks_data = json.load(f)
                    for task_data in tasks_data:
                        # 创建任务时，初始状态设为stopped
                        task = TaskRunner(
                            task_data["task_id"],
                            task_data["name"],
                            task_data["input_dir"],
                            task_data["output_dir"],
                            task_data["extensions"]
                        )
                        # 恢复任务的时间信息
                        task.start_time = task_data.get("start_time")
                        task.stop_time = task_data.get("stop_time")
                        self.tasks[task.task_id] = task
                        
                        # 如果配置文件中的状态是running，尝试启动任务
                        if task_data.get("status") == "running":
                            print(f"尝试恢复运行中的任务: {task.name}")
                            if not task.start():  # 如果启动失败
                                print(f"任务 {task.name} 恢复失败，标记为已停止")
                                task.status = "stopped"
                                task.stop_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            
                # 保存任务状态
                self.save_tasks()
            except Exception as e:
                print(f"加载任务配置失败: {str(e)}")

    def save_tasks(self):
        """保存任务到配置文件"""
        try:
            tasks_data = [task.to_dict() for task in self.tasks.values()]
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(tasks_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存任务配置失败: {str(e)}")

    def add_task(self, name, input_dir, output_dir, extensions):
        """添加新任务"""
        task_id = str(int(time.time()))
        task = TaskRunner(task_id, name, input_dir, output_dir, extensions)
        self.tasks[task_id] = task
        self.save_tasks()
        return task_id

    def remove_task(self, task_id):
        """删除任务"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if task.status == "running":
                task.stop()
            del self.tasks[task_id]
            self.save_tasks()
            return True
        return False

    def start_task(self, task_id):
        """启动任务"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if task.start():
                self.save_tasks()
                return True
        return False

    def stop_task(self, task_id):
        """停止任务"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if task.stop():
                self.save_tasks()
                return True
        return False

    def get_task(self, task_id):
        """获取任务信息"""
        return self.tasks.get(task_id)

    def get_all_tasks(self):
        """获取所有任务信息"""
        return [task.to_dict() for task in self.tasks.values()]

    def update_task(self, task_id, name=None, input_dir=None, output_dir=None, extensions=None):
        """更新任务信息"""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            if task.status == "running":
                return False

            if name is not None:
                task.name = name
            if input_dir is not None:
                task.input_dir = input_dir
            if output_dir is not None:
                task.output_dir = output_dir
            if extensions is not None:
                task.extensions = extensions

            self.save_tasks()
            return True
        return False 