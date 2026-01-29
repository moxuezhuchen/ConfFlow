#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库模块

管理计算任务结果的 SQLite 存储，支持任务状态跟踪和断点续算。
"""

import sqlite3
import json
import logging
import os
import shutil
import tempfile
from typing import Dict, Any, List, Optional


logger = logging.getLogger("confflow.calc.database")


class ResultsDB:
    """任务结果数据库管理器。

    使用 SQLite 存储计算任务的结果，支持：
    - 任务状态跟踪（成功/失败/跳过）
    - 能量和频率数据存储
    - 最终结构坐标保存
    - TS 键长和热力学校正保存

    Attributes:
        db_path: 数据库文件路径
        conn: SQLite 连接对象
    """

    def __init__(self, db_path: str):
        """初始化数据库连接。

        Args:
            db_path: SQLite 数据库文件路径
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_table()

    def _create_table(self) -> None:
        """创建任务结果表（如不存在）。"""
        # 启用 WAL 模式以提高并发性能
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_results (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                task_index INTEGER,
                status TEXT NOT NULL,
                energy REAL,
                final_gibbs_energy REAL,
                final_sp_energy REAL,
                num_imag_freqs INTEGER,
                lowest_freq REAL,
                g_corr REAL,
                ts_bond_atoms TEXT,
                ts_bond_length REAL,
                final_coords TEXT,
                error TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # 兼容旧数据库：补齐可能缺失的列
        try:
            cols = {r[1] for r in self.conn.execute("PRAGMA table_info(task_results)")}
            if "ts_bond_atoms" not in cols:
                self.conn.execute("ALTER TABLE task_results ADD COLUMN ts_bond_atoms TEXT")
            if "ts_bond_length" not in cols:
                self.conn.execute("ALTER TABLE task_results ADD COLUMN ts_bond_length REAL")
        except sqlite3.OperationalError as e:
            logger.warning(f"数据库列检查失败（操作错误）: {e}")
        except sqlite3.DatabaseError as e:
            logger.warning(f"数据库列检查失败（数据库错误）: {e}")

        self.conn.commit()

    def insert_result(self, task_info: Dict[str, Any]) -> int:
        """插入任务结果。

        Args:
            task_info: 任务结果字典，包含 job_name, status, energy 等字段

        Returns:
            插入的记录 ID
        """
        cursor = self.conn.execute(
            """
            INSERT INTO task_results (
                job_name, task_index, status, energy, 
                final_gibbs_energy, final_sp_energy, num_imag_freqs,
                lowest_freq, g_corr, ts_bond_atoms, ts_bond_length, 
                final_coords, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                task_info.get("job_name"),
                task_info.get("index"),
                task_info.get("status"),
                task_info.get("energy"),
                task_info.get("final_gibbs_energy"),
                task_info.get("final_sp_energy"),
                task_info.get("num_imag_freqs"),
                task_info.get("lowest_freq"),
                task_info.get("g_corr"),
                task_info.get("ts_bond_atoms"),
                task_info.get("ts_bond_length"),
                (
                    json.dumps(task_info.get("final_coords"))
                    if task_info.get("final_coords")
                    else None
                ),
                task_info.get("error"),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def get_all_results(self) -> List[Dict[str, Any]]:
        """获取所有任务结果。

        Returns:
            按任务索引排序的结果列表
        """
        cursor = self.conn.execute("SELECT * FROM task_results ORDER BY task_index")
        return [self._row_to_dict(row) for row in cursor]

    def get_result_by_job_name(self, job_name: str) -> Optional[Dict[str, Any]]:
        """根据任务名查询结果。

        Args:
            job_name: 任务名称（如 geom_0001）

        Returns:
            任务结果字典，不存在则返回 None
        """
        cursor = self.conn.execute("SELECT * FROM task_results WHERE job_name = ?", (job_name,))
        row = cursor.fetchone()
        return self._row_to_dict(row) if row else None

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """将数据库行转换为字典。"""
        return {
            "index": row["task_index"],
            "job_name": row["job_name"],
            "status": row["status"],
            "energy": row["energy"],
            "final_gibbs_energy": row["final_gibbs_energy"],
            "final_sp_energy": row["final_sp_energy"],
            "num_imag_freqs": row["num_imag_freqs"],
            "lowest_freq": row["lowest_freq"],
            "g_corr": row["g_corr"],
            "ts_bond_atoms": row["ts_bond_atoms"] if "ts_bond_atoms" in row.keys() else None,
            "ts_bond_length": row["ts_bond_length"] if "ts_bond_length" in row.keys() else None,
            "final_coords": json.loads(row["final_coords"]) if row["final_coords"] else None,
            "error": row["error"],
        }

    def backup(self, backup_path: Optional[str] = None) -> str:
        """备份数据库到指定路径（原子操作）。

        Args:
            backup_path: 备份文件路径，默认为 db_path + '.backup'

        Returns:
            备份文件路径
        """
        if backup_path is None:
            backup_path = self.db_path + ".backup"

        # 使用临时文件 + 原子重命名确保完整性
        fd, tmp_path = tempfile.mkstemp(suffix=".db", dir=os.path.dirname(self.db_path))
        os.close(fd)

        try:
            backup_conn = sqlite3.connect(tmp_path)
            with backup_conn:
                self.conn.backup(backup_conn)
            backup_conn.close()

            # 原子重命名
            shutil.move(tmp_path, backup_path)
            logger.debug(f"数据库已备份到: {backup_path}")
            return backup_path
        except Exception as e:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            logger.warning(f"数据库备份失败: {e}")
            raise

    def close(self) -> None:
        """关闭数据库连接。"""
        self.conn.close()
