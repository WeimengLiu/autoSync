#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
from task_manager import TaskManager
import os
from datetime import datetime
import time
import asyncio

app = Flask(__name__)
task_manager = TaskManager()

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """获取所有任务"""
    return jsonify(task_manager.get_all_tasks())

@app.route('/api/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    """获取单个任务信息"""
    task = task_manager.get_task(task_id)
    if task:
        return jsonify(task.to_dict())
    return jsonify({"error": "Task not found"}), 404

@app.route('/api/tasks', methods=['POST'])
def add_task():
    """添加新任务"""
    data = request.json
    task_id = task_manager.add_task(
        data['name'],
        data['input_dir'],
        data['output_dir'],
        data['extensions']
    )
    return jsonify({"task_id": task_id})

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def remove_task(task_id):
    """删除任务"""
    success = task_manager.remove_task(task_id)
    return jsonify({"success": success})

@app.route('/api/tasks/<task_id>/sync', methods=['POST'])
def sync_task(task_id):
    """执行全量同步"""
    try:
        task = task_manager.get_task(task_id)
        if not task:
            return jsonify({"success": False, "message": "任务不存在"})
        
        # 确保logger已初始化
        from sync_files import sync_all_files, cleanup_empty_dirs, setup_logger
        if task.logger is None:
            task.logger = setup_logger(verbose=True, task_id=task.task_id)
        
        # 执行全量同步
        asyncio.run(sync_all_files(task.input_dir, task.output_dir, task.extensions, task.logger))
        cleanup_empty_dirs(task.output_dir, task.logger)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/tasks/<task_id>/start', methods=['POST'])
def start_task(task_id):
    """启动任务"""
    try:
        if task_manager.start_task(task_id):
            return jsonify({"success": True, "message": "任务启动成功"})
        else:
            return jsonify({"success": False, "message": "任务已经在运行中"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/tasks/<task_id>/stop', methods=['POST'])
def stop_task(task_id):
    """停止任务"""
    try:
        if task_manager.stop_task(task_id):
            return jsonify({"success": True, "message": "任务停止成功"})
        else:
            return jsonify({"success": False, "message": "任务未在运行"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    """更新任务"""
    data = request.json
    success = task_manager.update_task(
        task_id,
        data.get('name'),
        data.get('input_dir'),
        data.get('output_dir'),
        data.get('extensions')
    )
    return jsonify({"success": success})

@app.route('/api/tasks/<task_id>/logs', methods=['GET'])
def get_task_logs(task_id):
    """获取任务日志"""
    # 获取日期参数，默认为当天
    date_str = request.args.get('date', datetime.now().strftime("%Y%m%d"))
    
    # 构建带任务ID的日志文件名
    log_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'logs',
        f'file_sync_{task_id}_{date_str}.log'
    )

    print(f"尝试读取日志文件: {log_file}")

    if not os.path.exists(log_file):
        print(f"日志文件不存在: {log_file}")
        return jsonify({"logs": []})

    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = f.readlines()
        print(f"成功读取日志文件，共 {len(logs)} 行")
        return jsonify({"logs": logs})
    except Exception as e:
        print(f"读取日志文件失败: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/tasks/<task_id>/log_dates', methods=['GET'])
def get_log_dates(task_id):
    """获取可用的日志日期列表"""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    print(f"查找日志目录: {log_dir}")
    
    if not os.path.exists(log_dir):
        print(f"日志目录不存在: {log_dir}")
        return jsonify({"dates": []})

    dates = []
    prefix = f'file_sync_{task_id}_'
    for filename in os.listdir(log_dir):
        if filename.startswith(prefix) and filename.endswith('.log'):
            # 从文件名中提取日期部分
            date_str = filename[len(prefix):-4]  # 去掉前缀和.log后缀
            if len(date_str) == 8 and date_str.isdigit():  # 确保日期格式正确
                dates.append(date_str)
                print(f"找到日志文件: {filename}, 日期: {date_str}")

    # 按日期降序排序
    dates.sort(reverse=True)
    print(f"找到 {len(dates)} 个日志文件")
    return jsonify({"dates": dates})

if __name__ == '__main__':
    # 禁用调试模式
    app.run(host='0.0.0.0', port=5001, debug=False) 