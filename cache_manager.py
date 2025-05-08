from sqlalchemy import create_engine, Column, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import StaticPool
from datetime import datetime, timedelta
import os
import logging
from pathlib import Path
from typing import Optional, Tuple

Base = declarative_base()

class FileCache(Base):
    """文件缓存表"""
    __tablename__ = 'file_cache'

    file_path = Column(String, primary_key=True)
    task_id = Column(String, primary_key=True)
    md5_hash = Column(String)
    mtime = Column(Float)
    last_check = Column(DateTime, default=datetime.now)

class CacheManager:
    _instance = None
    _engine = None
    _session_factory = None

    def __new__(cls, task_id: str, logger: logging.Logger):
        if cls._instance is None:
            cls._instance = super(CacheManager, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def __init__(self, task_id: str, logger: logging.Logger):
        if not hasattr(self, 'task_id'):  # 只在第一次初始化
            self.task_id = task_id
            self.logger = logger
            self.logger.info(f"已初始化缓存数据库: {self._get_db_path()}")

    def _init_db(self) -> None:
        """初始化数据库"""
        if self._engine is None:
            # 确保数据库目录存在
            db_path = self._get_db_path()
            db_path.parent.mkdir(exist_ok=True)
            
            # 创建数据库连接
            self._engine = create_engine(
                f'sqlite:///{db_path}',
                connect_args={'check_same_thread': False},
                poolclass=StaticPool,
                echo=False
            )
            
            # 创建表
            Base.metadata.create_all(self._engine)
            
            # 创建会话工厂
            self._session_factory = scoped_session(sessionmaker(bind=self._engine))

    def _get_db_path(self) -> Path:
        """获取数据库路径"""
        return Path(os.path.dirname(os.path.abspath(__file__))) / '.db' / 'file_cache.db'

    def get_cache(self, file_path: str) -> Tuple[Optional[str], Optional[float]]:
        """获取文件的缓存信息"""
        session = self._session_factory()
        try:
            cache = session.query(FileCache).filter_by(
                file_path=file_path,
                task_id=self.task_id
            ).first()
            
            if cache:
                cache.last_check = datetime.now()
                session.commit()
                return cache.md5_hash, cache.mtime
            return None, None
        except Exception as e:
            self.logger.error(f"获取缓存失败: {e}")
            return None, None
        finally:
            session.close()

    def update_cache(self, file_path: str, md5_hash: str, mtime: float) -> None:
        """更新文件的缓存信息"""
        session = self._session_factory()
        try:
            cache = session.query(FileCache).filter_by(
                file_path=file_path,
                task_id=self.task_id
            ).first()
            
            if cache:
                cache.md5_hash = md5_hash
                cache.mtime = mtime
                cache.last_check = datetime.now()
            else:
                cache = FileCache(
                    file_path=file_path,
                    task_id=self.task_id,
                    md5_hash=md5_hash,
                    mtime=mtime
                )
                session.add(cache)
            
            session.commit()
            self.logger.debug(f"已更新缓存: {file_path}")
        except Exception as e:
            self.logger.error(f"更新缓存失败: {e}")
            session.rollback()
        finally:
            session.close()

    def cleanup_old_records(self, days: int = 30) -> None:
        """清理指定天数之前的缓存记录"""
        session = self._session_factory()
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            session.query(FileCache).filter(
                FileCache.last_check < cutoff_date
            ).delete(synchronize_session=False)
            session.commit()
            self.logger.info(f"已清理 {days} 天前的缓存记录")
        except Exception as e:
            self.logger.error(f"清理缓存失败: {e}")
            session.rollback()
        finally:
            session.close()

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        session = self._session_factory()
        try:
            total_records = session.query(FileCache).count()
            task_records = session.query(FileCache).filter_by(task_id=self.task_id).count()
            latest_record = session.query(FileCache.last_check).order_by(
                FileCache.last_check.desc()
            ).first()
            oldest_record = session.query(FileCache.last_check).order_by(
                FileCache.last_check.asc()
            ).first()
            
            return {
                "total_records": total_records,
                "task_records": task_records,
                "latest_update": latest_record[0].isoformat() if latest_record else None,
                "oldest_record": oldest_record[0].isoformat() if oldest_record else None
            }
        except Exception as e:
            self.logger.error(f"获取统计信息失败: {e}")
            return {}
        finally:
            session.close() 