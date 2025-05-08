# AutoSync 文件自动同步工具

AutoSync 是一个基于 Python 的文件自动同步工具，它能够实时监控指定目录的文件变化，并自动将变化同步到目标目录。

## 主要特性

- 🔄 实时监控：使用 `pyinotify` 实时监控文件系统变化
- 🚀 并行处理：使用多进程进行文件同步，提高处理速度
- 💾 智能缓存：使用 MD5 缓存机制，避免重复同步相同内容的文件
- 🎯 选择性同步：支持指定文件后缀进行同步
- 🔗 软链接支持：对于不在同步列表中的文件，创建软链接而不是复制
- 📊 进度显示：同步过程中实时显示进度
- 🌐 Web界面：提供友好的Web管理界面

## 安装要求

- Python 3.6+
- Flask
- pyinotify（Linux系统）
- watchdog（Windows/MacOS系统）

## 快速开始

1. 克隆仓库：
```bash
git clone https://github.com/WeimengLiu/autoSync.git
cd autoSync
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 启动服务：
```bash
python app.py
```

4. 打开浏览器访问：`http://localhost:5001`

## 使用说明

### Web界面操作

1. 添加同步任务：
   - 点击"添加新任务"按钮
   - 填写任务名称、输入目录、输出目录和文件后缀
   - 点击"添加"按钮保存

2. 管理任务：
   - 启动/停止：点击对应按钮控制任务运行状态
   - 立即执行：手动触发一次全量同步
   - 查看日志：实时查看同步日志
   - 编辑/删除：修改任务配置或删除任务

### 文件同步规则

- 在同步列表中的文件后缀：直接复制文件
- 不在同步列表中的文件：创建软链接
- 删除文件：自动删除目标目录对应文件
- 删除目录：递归删除目标目录及其内容

## 配置说明

### 默认支持的文件类型

```
jpg, jpeg, png, gif, bmp, webp, ico, svg,
nfo, srt, ass, ssa, sub, idx, smi, ssa, SRT, sup
```

### 日志文件

- 位置：`logs/file_sync_任务ID_日期.log`
- 格式：时间 - 级别 - 消息

### 缓存文件

- 位置：`cache/sync_cache_*.json`
- 内容：文件MD5值和修改时间缓存

## 性能优化

1. 并行处理：
   - 使用多进程进行文件同步
   - 进程数默认为 CPU 核心数的2倍

2. 缓存机制：
   - 缓存文件MD5值避免重复计算
   - 使用文件修改时间快速判断文件是否变化

3. 选择性同步：
   - 只同步指定类型的文件
   - 其他文件使用软链接节省空间

## 贡献指南

欢迎提交 Issue 和 Pull Request。在提交 PR 之前，请确保：

1. 代码符合 PEP 8 规范
2. 添加了必要的测试
3. 更新了相关文档

## 许可证

本项目基于 MIT 许可证开源，详见 [LICENSE](LICENSE) 文件。

## 生产环境部署

### 1. 使用 Supervisor 管理进程

1. 安装 Supervisor：
```bash
# Ubuntu/Debian
sudo apt-get install supervisor

# CentOS/RHEL
sudo yum install supervisor
```

2. 创建配置文件 `/etc/supervisor/conf.d/autosync.conf`：
```ini
[program:autosync]
directory=/path/to/autoSync
command=/path/to/venv/bin/python app.py
user=your_user
autostart=true
autorestart=true
stderr_logfile=/var/log/autosync/err.log
stdout_logfile=/var/log/autosync/out.log
environment=PYTHONUNBUFFERED=1

[supervisord]
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid
childlogdir=/var/log/supervisor
```

3. 创建日志目录：
```bash
sudo mkdir -p /var/log/autosync
sudo chown your_user:your_user /var/log/autosync
```

4. 启动服务：
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start autosync
```

### 2. 使用 Nginx 反向代理

1. 安装 Nginx：
```bash
# Ubuntu/Debian
sudo apt-get install nginx

# CentOS/RHEL
sudo yum install nginx
```

2. 创建 Nginx 配置文件 `/etc/nginx/conf.d/autosync.conf`：
```nginx
server {
    listen 80;
    server_name your_domain.com;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 用于处理大文件上传
    client_max_body_size 100M;
    
    # 安全相关配置
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Content-Type-Options "nosniff";
}
```

3. 测试并重启 Nginx：
```bash
sudo nginx -t
sudo systemctl restart nginx
```

### 3. 系统服务配置

1. 创建系统服务文件 `/etc/systemd/system/autosync.service`：
```ini
[Unit]
Description=AutoSync File Synchronization Service
After=network.target

[Service]
Type=simple
User=your_user
Group=your_user
WorkingDirectory=/path/to/autoSync
Environment="PATH=/path/to/venv/bin"
ExecStart=/path/to/venv/bin/python app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

2. 启用并启动服务：
```bash
sudo systemctl enable autosync
sudo systemctl start autosync
```

### 4. 安全配置

1. 配置防火墙：
```bash
# Ubuntu/Debian (UFW)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

2. 设置文件权限：
```bash
# 设置应用目录权限
sudo chown -R your_user:your_user /path/to/autoSync
sudo chmod -R 755 /path/to/autoSync

# 设置日志目录权限
sudo chown -R your_user:your_user /var/log/autosync
sudo chmod -R 755 /var/log/autosync
```

### 5. 监控和维护

1. 日志轮转配置 `/etc/logrotate.d/autosync`：
```
/var/log/autosync/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 your_user your_user
}
```

2. 监控命令：
```bash
# 查看服务状态
sudo systemctl status autosync

# 查看日志
sudo journalctl -u autosync

# 查看 Supervisor 状态
sudo supervisorctl status

# 查看 Nginx 访问日志
tail -f /var/log/nginx/access.log
```

### 6. 备份策略

1. 备份数据：
```bash
# 创建备份脚本 backup.sh
#!/bin/bash
BACKUP_DIR="/path/to/backups"
DATE=$(date +%Y%m%d)
tar -czf "$BACKUP_DIR/autosync_$DATE.tar.gz" \
    -C /path/to/autoSync \
    --exclude="*.pyc" \
    --exclude="__pycache__" \
    --exclude="logs/*" \
    --exclude="cache/*" \
    .
```

2. 设置定时备份（每天凌晨2点）：
```bash
# 添加到 crontab
0 2 * * * /path/to/backup.sh
```

### 7. 更新部署

1. 创建更新脚本 `update.sh`：
```bash
#!/bin/bash
# 停止服务
sudo systemctl stop autosync

# 备份当前版本
./backup.sh

# 拉取最新代码
git pull origin main

# 更新依赖
source venv/bin/activate
pip install -r requirements.txt

# 重启服务
sudo systemctl start autosync
```

2. 执行更新：
```bash
bash update.sh
``` 