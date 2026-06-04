"""
QuantBase 本地数据库
SQLite 数据库操作封装

优化:
- WAL 模式: 支持并发读写，读操作不阻塞写操作
- 线程安全连接池: 使用 threading.local 避免跨线程共享连接
- 连接复用: 同一线程内复用连接，减少创建/关闭开销
"""
import sqlite3
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
import platform
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class LocalDatabase:
    """本地 SQLite 数据库 (线程安全 + WAL 模式)"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            if settings.DB_PATH:
                db_path = settings.DB_PATH
            else:
                # 默认使用项目目录内的 data，避免系统目录权限导致启动失败
                project_root = Path(__file__).resolve().parents[3]
                db_path = str(project_root / "data" / "crypto_data.db")

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        # FastAPI 的同步数据库调用可能在不同 worker/thread 中执行。sqlite3 连接不能随意跨线程共享，
        # 所以这里用 threading.local 让每个线程复用自己的连接，同时避免频繁 connect/close。
        self._local = threading.local()

    def get_connection(self) -> sqlite3.Connection:
        """
        获取数据库连接 (线程安全)
        同一线程内复用连接，不同线程使用不同连接
        """
        conn = getattr(self._local, 'connection', None)

        # 检测连接是否仍然有效
        if conn is not None:
            try:
                conn.execute('SELECT 1')
            except Exception:
                conn = None
                self._local.connection = None

        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.row_factory = sqlite3.Row  # 支持字典访问

            # 启用 WAL 模式: 允许并发读写
            conn.execute('PRAGMA journal_mode=WAL')
            # 同步模式设为 NORMAL: 在 WAL 模式下兼顾性能和安全
            conn.execute('PRAGMA synchronous=NORMAL')
            # 增大缓存: 提高查询性能 (64MB)
            conn.execute('PRAGMA cache_size=-65536')
            # 启用外键约束
            conn.execute('PRAGMA foreign_keys=ON')
            # 增加 busy_timeout 防止 "database is locked"
            conn.execute('PRAGMA busy_timeout=5000')

            self._local.connection = conn
            logger.debug(f"New SQLite connection created for thread {threading.current_thread().name}")

        return conn

    def close_connection(self):
        """关闭当前线程的连接 (应用关闭时调用)"""
        conn = getattr(self._local, 'connection', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.connection = None

    def init_db(self):
        """
        初始化 SQLite schema。

        QuantBase 生产库会随 sprint 逐步演进，所以本方法既创建新表，也调用 `_ensure_*`
        这类轻量迁移方法补齐旧库缺失列。新增字段时优先追加迁移，不要假设线上库是全新初始化。
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        # ============================================
        # K线历史数据表 (旧统一表 — 保留兼容)
        # ============================================
        # Durable Data Manager 的主数据源已迁移到文件 K 线 store，但旧页面、旧部署和部分兼容路径
        # 仍可能从 SQLite 读写 K 线。保留统一表可以让老数据继续参与回测 fallback。
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kline_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                quote_volume REAL,
                trades_count INTEGER,
                UNIQUE(exchange, symbol, timeframe, timestamp)
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_kline_symbol_time
            ON kline_history(exchange, symbol, timeframe, timestamp)
        ''')

        # ============================================
        # K线分表 — 按 timeframe 拆分，提升查询性能
        # ============================================
        # 高频周期的数据量最大；分表后按 exchange/symbol/timestamp 查询会比旧统一表更稳定。
        # 不在集合内的周期仍落到旧统一表，避免新周期上线时直接破坏兼容性。
        for tf in ['1m', '5m', '15m', '1h', '4h', '1d']:
            table = f'kline_{tf}'
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    quote_volume REAL,
                    UNIQUE(exchange, symbol, timestamp)
                )
            ''')
            cursor.execute(f'''
                CREATE INDEX IF NOT EXISTS idx_{table}_sym_ts
                ON {table}(exchange, symbol, timestamp)
            ''')

        # ============================================
        # 资金费率历史表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS funding_rate_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                funding_rate REAL NOT NULL,
                mark_price REAL,
                UNIQUE(exchange, symbol, timestamp)
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_funding_symbol_time
            ON funding_rate_history(exchange, symbol, timestamp)
        ''')

        # ============================================
        # 资金费率实时表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS funding_rate_realtime (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                current_rate REAL,
                predicted_rate REAL,
                next_funding_time INTEGER,
                mark_price REAL,
                index_price REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, symbol)
            )
        ''')

        # ============================================
        # 持仓量历史表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS open_interest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open_interest REAL NOT NULL,
                open_interest_value REAL,
                UNIQUE(exchange, symbol, timestamp)
            )
        ''')

        # ============================================
        # 爆仓历史表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS liquidation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                value REAL NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_liq_time
            ON liquidation_history(timestamp)
        ''')

        # ============================================
        # 成交历史表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                quote_quantity REAL,
                is_maker INTEGER,
                UNIQUE(exchange, symbol, trade_id)
            )
        ''')

        # ============================================
        # 策略表
        # ============================================
        # strategies 是策略定义和模拟盘实例的核心索引。config/symbols 存 JSON，运行态只保留
        # 状态和本轮 run_started_at；成交、权益曲线等时序数据放在独立表里，便于重启恢复。
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                script_content TEXT NOT NULL,
                config TEXT,
                status TEXT DEFAULT 'stopped',
                exchange TEXT,
                symbols TEXT,
                run_started_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self._ensure_strategies_run_started_at_column(cursor)

        # ============================================
        # 实盘工作台策略设置表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_strategy_settings (
                strategy_id INTEGER PRIMARY KEY,
                added INTEGER NOT NULL DEFAULT 0,
                account_id TEXT DEFAULT 'default',
                deployment_strategy_id INTEGER,
                status TEXT DEFAULT 'added',
                risk_config TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        ''')

        # ============================================
        # 策略交易记录表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                order_id TEXT,
                timestamp INTEGER NOT NULL,
                side TEXT NOT NULL,
                type TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                fee REAL,
                fee_asset TEXT,
                pnl REAL,
                meta TEXT,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        ''')
        self._ensure_strategy_trades_meta_column(cursor)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_strategy_trades_id
            ON strategy_trades(strategy_id, timestamp)
        ''')

        # ============================================
        # 策略权益曲线采样表（服务重启后恢复模拟盘账户曲线）
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_equity_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                equity REAL NOT NULL,
                balance REAL,
                realized_pnl REAL,
                unrealized_pnl REAL,
                total_pnl REAL,
                drawdown_pct REAL,
                return_pct REAL,
                win_rate REAL,
                profit_factor REAL,
                source TEXT DEFAULT 'runtime',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(strategy_id, timestamp),
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        ''')
        self._ensure_strategy_equity_sample_metric_columns(cursor)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_strategy_equity_samples_id_time
            ON strategy_equity_samples(strategy_id, timestamp)
        ''')

        # ============================================
        # 回测结果表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                final_capital REAL,
                total_return REAL,
                annual_return REAL,
                max_drawdown REAL,
                sharpe_ratio REAL,
                win_rate REAL,
                profit_factor REAL,
                total_trades INTEGER,
                trades_detail TEXT,
                timeframe TEXT,
                timeframe_mode TEXT,
                matrix_results_json TEXT,
                result_json TEXT,
                status TEXT DEFAULT 'running',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        ''')
        self._ensure_backtest_result_timeframe_columns(cursor)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backtest_results_created
            ON backtest_results(created_at, id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backtest_results_return
            ON backtest_results(total_return, created_at, id)
        ''')

        # ============================================
        # 回测异步任务（进度持久化，服务重启后可查询）
        # ============================================
        # Backtest 页面允许多个 job 并发。任务状态落库后，服务重启时 main.py 可以把内存中断的
        # pending/running/cancelling 标记为 interrupted，而不是让前端误以为它们仍在运行。
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_jobs (
                job_id TEXT PRIMARY KEY,
                strategy_id INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                current_bar INTEGER DEFAULT 0,
                total_bars INTEGER DEFAULT 0,
                message TEXT,
                result_json TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backtest_jobs_status
            ON backtest_jobs(status, updated_at)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backtest_jobs_updated
            ON backtest_jobs(updated_at)
        ''')
        self._ensure_backtest_job_auth_columns(cursor)

        # ============================================
        # 登录会话与临时邀请码
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                session_hash TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                guest_code_id INTEGER,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_hash
            ON auth_sessions(session_hash)
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS guest_access_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash TEXT NOT NULL UNIQUE,
                note TEXT DEFAULT '',
                expires_at TEXT NOT NULL,
                max_backtests_per_day INTEGER DEFAULT 10,
                max_concurrent_backtests INTEGER DEFAULT 1,
                max_backtest_days INTEGER DEFAULT 365,
                created_by TEXT DEFAULT 'admin',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT,
                revoked_at TEXT
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_guest_access_codes_hash
            ON guest_access_codes(code_hash)
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS auth_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                role TEXT,
                session_id TEXT,
                guest_code_id INTEGER,
                success INTEGER DEFAULT 1,
                reason TEXT,
                ip_address TEXT,
                user_agent TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_auth_audit_events_created
            ON auth_audit_events(created_at, event_type)
        ''')

        # ============================================
        # 告警配置表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                symbol TEXT,
                condition TEXT NOT NULL,
                notification TEXT,
                enabled INTEGER DEFAULT 1,
                last_triggered_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ============================================
        # 监控中心运行策略收益卡片推送配置
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitor_profit_push_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 0,
                interval_minutes INTEGER DEFAULT 60,
                running INTEGER DEFAULT 0,
                last_started_at TEXT,
                last_sent_at TEXT,
                last_finished_at TEXT,
                last_error TEXT,
                last_skip_reason TEXT,
                webhook_url TEXT,
                updated_at TEXT
            )
        ''')
        cursor.execute("PRAGMA table_info(monitor_profit_push_config)")
        profit_push_columns = {row['name'] for row in cursor.fetchall()}
        if "webhook_url" not in profit_push_columns:
            cursor.execute("ALTER TABLE monitor_profit_push_config ADD COLUMN webhook_url TEXT")
        cursor.execute('''
            INSERT OR IGNORE INTO monitor_profit_push_config
            (id, enabled, interval_minutes, running, updated_at)
            VALUES (1, 0, 60, 0, ?)
        ''', (datetime.now().isoformat(),))

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_profit_push_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 0,
                interval_minutes INTEGER DEFAULT 60,
                running INTEGER DEFAULT 0,
                last_started_at TEXT,
                last_sent_at TEXT,
                last_finished_at TEXT,
                last_error TEXT,
                last_skip_reason TEXT,
                updated_at TEXT
            )
        ''')
        cursor.execute('''
            INSERT OR IGNORE INTO live_profit_push_config
            (id, enabled, interval_minutes, running, updated_at)
            VALUES (1, 0, 60, 0, ?)
        ''', (datetime.now().isoformat(),))

        # ============================================
        # 应用设置表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        ''')

        # ============================================
        # 交易所配置表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS exchange_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL UNIQUE,
                api_key TEXT,
                api_secret TEXT,
                passphrase TEXT,
                testnet INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # ============================================
        # 实盘账户表：支持多个 OKX API Key，密钥只在服务端使用
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'okx',
                api_key TEXT NOT NULL,
                api_secret TEXT NOT NULL,
                passphrase TEXT,
                testnet INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                can_trade INTEGER,
                permission_checked_at TEXT,
                permission_check_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_strategy_account_bindings (
                strategy_id INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                added INTEGER NOT NULL DEFAULT 1,
                deployment_strategy_id INTEGER,
                status TEXT DEFAULT 'added',
                risk_config TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (strategy_id, account_id),
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_strategy_id INTEGER NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'okx',
                market_type TEXT NOT NULL DEFAULT 'swap',
                signal_action TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT,
                price REAL,
                notional_usdt REAL,
                quantity REAL,
                leverage REAL,
                margin REAL,
                paper_trade_id TEXT,
                paper_status TEXT,
                live_dispatch_status TEXT NOT NULL DEFAULT 'pending',
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_strategy_signal_events_source
            ON strategy_signal_events(source_strategy_id, created_at)
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_strategy_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_strategy_id INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                risk_config TEXT,
                last_signal_event_id INTEGER,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paused_at TEXT,
                stopped_at TEXT,
                UNIQUE(source_strategy_id, account_id)
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_live_strategy_subscriptions_lookup
            ON live_strategy_subscriptions(source_strategy_id, account_id, status)
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_signal_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_event_id INTEGER NOT NULL,
                subscription_id INTEGER NOT NULL,
                source_strategy_id INTEGER NOT NULL,
                account_id TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'okx',
                status TEXT NOT NULL,
                live_order_id TEXT,
                request_payload TEXT,
                response_payload TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_live_signal_executions_signal
            ON live_signal_executions(signal_event_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_live_signal_executions_subscription
            ON live_signal_executions(subscription_id, created_at)
        ''')

        # ============================================
        # 数据同步元数据表 — 记录每个交易对的同步进度
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                data_type TEXT NOT NULL DEFAULT 'kline',
                first_timestamp INTEGER,
                last_timestamp INTEGER,
                total_records INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                last_sync_at TIMESTAMP,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, symbol, timeframe, data_type)
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sync_meta
            ON sync_metadata(exchange, symbol, timeframe, data_type)
        ''')

        # ============================================
        # 数据同步任务表 — 支持服务重启后断点续传
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_jobs (
                id TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                symbols_json TEXT NOT NULL,
                timeframes_json TEXT NOT NULL,
                history_days INTEGER DEFAULT 365,
                start_date TEXT,
                end_date TEXT,
                total_symbols INTEGER DEFAULT 0,
                total_timeframes INTEGER DEFAULT 0,
                total_records_fetched INTEGER DEFAULT 0,
                total_records_inserted INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sync_jobs_status
            ON sync_jobs(status, updated_at)
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sync_job_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                total_fetched INTEGER DEFAULT 0,
                total_inserted INTEGER DEFAULT 0,
                checkpoint_timestamp INTEGER,
                started_at TIMESTAMP,
                ended_at TIMESTAMP,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(job_id, symbol, timeframe),
                FOREIGN KEY(job_id) REFERENCES sync_jobs(id) ON DELETE CASCADE
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sync_job_items_job_status
            ON sync_job_items(job_id, status, id)
        ''')

        # ============================================
        # 行情缓存表 (Ticker)
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ticker_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last REAL,
                bid REAL,
                ask REAL,
                high REAL,
                low REAL,
                volume REAL,
                quote_volume REAL,
                change REAL,
                change_percent REAL,
                timestamp INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, symbol)
            )
        ''')

        # ============================================
        # Agent 任务表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'pending',
                stage TEXT DEFAULT 'planner',
                stage_label TEXT DEFAULT '',
                goal_criteria TEXT,
                market_type TEXT DEFAULT 'spot',
                symbol TEXT,
                timeframe TEXT,
                backtest_start TEXT,
                backtest_end TEXT,
                max_iterations INTEGER DEFAULT 10,
                current_iteration INTEGER DEFAULT 0,
                best_iteration INTEGER,
                user_prompt TEXT DEFAULT '',
                llm_model TEXT DEFAULT '',
                strategy_spec TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        self._ensure_agent_task_columns(cursor)

        # ============================================
        # Agent 迭代记录表
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_iterations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                iteration INTEGER NOT NULL,
                strategy_name TEXT,
                strategy_code TEXT,
                setup_code TEXT,
                reasoning TEXT,
                backtest_metrics TEXT,
                analysis TEXT,
                suggestions TEXT,
                eval_scores TEXT,
                contract TEXT,
                action TEXT DEFAULT 'new',
                score REAL DEFAULT 0,
                meets_goal INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TEXT,
                FOREIGN KEY (task_id) REFERENCES agent_tasks(id)
            )
        ''')
        self._ensure_agent_iteration_columns(cursor)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_agent_iter_task
            ON agent_iterations(task_id, iteration)
        ''')

        # ============================================
        # AI Lab 现有策略自动优化
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_optimizer_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER DEFAULT 0,
                interval_hours REAL DEFAULT 4,
                low_return_pct REAL DEFAULT 0,
                trial_hours REAL DEFAULT 4,
                trial_success_return_pct REAL DEFAULT 0,
                llm_model TEXT DEFAULT '',
                running INTEGER DEFAULT 0,
                last_started_at TEXT,
                last_finished_at TEXT,
                last_error TEXT,
                updated_at TEXT
            )
        ''')
        self._ensure_strategy_optimizer_config_columns(cursor)
        cursor.execute('''
            INSERT OR IGNORE INTO strategy_optimizer_config
            (id, enabled, interval_hours, low_return_pct, trial_hours, trial_success_return_pct, running, updated_at)
            VALUES (1, 0, 4, 0, 4, 0, 0, ?)
        ''', (datetime.now().isoformat(),))
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_optimization_runs (
                id TEXT PRIMARY KEY,
                source_strategy_id INTEGER NOT NULL,
                source_strategy_name TEXT,
                candidate_strategy_id INTEGER,
                agent_task_id TEXT,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                source_return_pct REAL,
                candidate_return_pct REAL,
                source_snapshot TEXT,
                ai_analysis TEXT,
                backtest_result TEXT,
                trial_started_at TEXT,
                trial_checked_at TEXT,
                trial_finished_at TEXT,
                error_message TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_strategy_opt_runs_status
            ON strategy_optimization_runs(status, updated_at)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_strategy_opt_runs_source
            ON strategy_optimization_runs(source_strategy_id, status)
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_optimization_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                stage TEXT,
                message TEXT,
                detail TEXT
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_strategy_opt_events_run
            ON strategy_optimization_events(run_id, id)
        ''')

        # ============================================
        # AI K 线预测持久化（复盘 / 对比）
        # ============================================
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ai_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                target_timestamp INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                quote_volume REAL,
                predicted_at INTEGER NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ai_pred_lookup
            ON ai_predictions(exchange, symbol, timeframe, target_timestamp)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ai_pred_lookup_latest
            ON ai_predictions(exchange, symbol, timeframe, target_timestamp, predicted_at DESC)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_ai_pred_predicted_at
            ON ai_predictions(exchange, symbol, timeframe, predicted_at)
        ''')
        self._ensure_ai_predictions_volume_columns(cursor)

        conn.commit()

    @staticmethod
    def _ensure_backtest_job_auth_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(backtest_jobs)")
        columns = {row["name"] for row in cursor.fetchall()}
        migrations = {
            "owner_role": "ALTER TABLE backtest_jobs ADD COLUMN owner_role TEXT",
            "owner_session_id": "ALTER TABLE backtest_jobs ADD COLUMN owner_session_id TEXT",
            "owner_guest_code_id": "ALTER TABLE backtest_jobs ADD COLUMN owner_guest_code_id INTEGER",
        }
        for column, sql in migrations.items():
            if column not in columns:
                cursor.execute(sql)
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_backtest_jobs_owner
            ON backtest_jobs(owner_role, owner_session_id, owner_guest_code_id, created_at)
        ''')

    @staticmethod
    def _ensure_strategies_run_started_at_column(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(strategies)")
        cols = {row[1] for row in cursor.fetchall()}
        if "run_started_at" not in cols:
            cursor.execute("ALTER TABLE strategies ADD COLUMN run_started_at TEXT")
            cursor.execute("""
                UPDATE strategies SET run_started_at = updated_at
                WHERE status = 'running'
                  AND (run_started_at IS NULL OR run_started_at = '')
            """)

    @staticmethod
    def _ensure_strategy_trades_meta_column(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(strategy_trades)")
        cols = {row[1] for row in cursor.fetchall()}
        if "meta" not in cols:
            cursor.execute("ALTER TABLE strategy_trades ADD COLUMN meta TEXT")

    @staticmethod
    def _ensure_strategy_equity_sample_metric_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(strategy_equity_samples)")
        cols = {row[1] for row in cursor.fetchall()}
        additions = {
            "total_pnl": "ALTER TABLE strategy_equity_samples ADD COLUMN total_pnl REAL",
            "win_rate": "ALTER TABLE strategy_equity_samples ADD COLUMN win_rate REAL",
            "profit_factor": "ALTER TABLE strategy_equity_samples ADD COLUMN profit_factor REAL",
        }
        for column, statement in additions.items():
            if column not in cols:
                cursor.execute(statement)

    @staticmethod
    def _ensure_backtest_result_timeframe_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(backtest_results)")
        cols = {row[1] for row in cursor.fetchall()}
        additions = {
            "timeframe": "ALTER TABLE backtest_results ADD COLUMN timeframe TEXT",
            "timeframe_mode": "ALTER TABLE backtest_results ADD COLUMN timeframe_mode TEXT",
            "matrix_results_json": "ALTER TABLE backtest_results ADD COLUMN matrix_results_json TEXT",
            "result_json": "ALTER TABLE backtest_results ADD COLUMN result_json TEXT",
        }
        for column, statement in additions.items():
            if column not in cols:
                cursor.execute(statement)

    @staticmethod
    def _ensure_ai_predictions_volume_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(ai_predictions)")
        cols = {row[1] for row in cursor.fetchall()}
        if "volume" not in cols:
            cursor.execute("ALTER TABLE ai_predictions ADD COLUMN volume REAL")
        if "quote_volume" not in cols:
            cursor.execute("ALTER TABLE ai_predictions ADD COLUMN quote_volume REAL")

    @staticmethod
    def _ensure_agent_task_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(agent_tasks)")
        cols = {row[1] for row in cursor.fetchall()}
        additions = {
            "stage": "ALTER TABLE agent_tasks ADD COLUMN stage TEXT DEFAULT 'planner'",
            "stage_label": "ALTER TABLE agent_tasks ADD COLUMN stage_label TEXT DEFAULT ''",
            "strategy_spec": "ALTER TABLE agent_tasks ADD COLUMN strategy_spec TEXT",
            "market_type": "ALTER TABLE agent_tasks ADD COLUMN market_type TEXT DEFAULT 'spot'",
            "llm_model": "ALTER TABLE agent_tasks ADD COLUMN llm_model TEXT DEFAULT ''",
        }
        for col, sql in additions.items():
            if col not in cols:
                cursor.execute(sql)

    @staticmethod
    def _ensure_agent_iteration_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(agent_iterations)")
        cols = {row[1] for row in cursor.fetchall()}
        additions = {
            "eval_scores": "ALTER TABLE agent_iterations ADD COLUMN eval_scores TEXT",
            "contract": "ALTER TABLE agent_iterations ADD COLUMN contract TEXT",
            "action": "ALTER TABLE agent_iterations ADD COLUMN action TEXT DEFAULT 'new'",
        }
        for col, sql in additions.items():
            if col not in cols:
                cursor.execute(sql)

    @staticmethod
    def _ensure_strategy_optimizer_config_columns(cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(strategy_optimizer_config)")
        cols = {row[1] for row in cursor.fetchall()}
        additions = {
            "llm_model": "ALTER TABLE strategy_optimizer_config ADD COLUMN llm_model TEXT DEFAULT ''",
        }
        for col, sql in additions.items():
            if col not in cols:
                cursor.execute(sql)

    # ============================================
    # K线分表名映射
    # ============================================
    _KLINE_SPLIT_TABLES = {'1m', '5m', '15m', '1h', '4h', '1d'}

    def _kline_table(self, timeframe: str) -> str:
        """根据 timeframe 返回分表名；不支持的周期回退到旧统一表，保持历史数据可读。"""
        if timeframe in self._KLINE_SPLIT_TABLES:
            return f'kline_{timeframe}'
        return 'kline_history'

    # ============================================
    # K线数据操作
    # ============================================

    def insert_klines(self, exchange: str, symbol: str, timeframe: str, klines: List[Dict]):
        """批量插入 K 线数据，同时写入分表和旧统一表。"""
        if not klines:
            return 0

        conn = self.get_connection()
        cursor = conn.cursor()

        # 1. 旧统一表是兼容兜底：老查询和非标准 timeframe 仍能拿到真实 K 线。
        legacy_data = [
            (
                exchange, symbol, timeframe,
                kline['timestamp'], kline['open'], kline['high'],
                kline['low'], kline['close'], kline['volume'],
                kline.get('quote_volume')
            )
            for kline in klines
        ]
        cursor.executemany('''
            INSERT OR IGNORE INTO kline_history
            (exchange, symbol, timeframe, timestamp, open, high, low, close, volume, quote_volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', legacy_data)

        # 2. 常用 timeframe 再写一份分表，加速页面和回测的窄范围读取。
        inserted = 0
        if timeframe in self._KLINE_SPLIT_TABLES:
            table = self._kline_table(timeframe)
            split_data = [
                (
                    exchange, symbol,
                    kline['timestamp'], kline['open'], kline['high'],
                    kline['low'], kline['close'], kline['volume'],
                    kline.get('quote_volume')
                )
                for kline in klines
            ]
            cursor.executemany(f'''
                INSERT OR IGNORE INTO {table}
                (exchange, symbol, timestamp, open, high, low, close, volume, quote_volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', split_data)
            inserted = cursor.rowcount
        else:
            inserted = cursor.rowcount

        conn.commit()
        conn.close()
        return inserted

    def get_klines(self, exchange: str, symbol: str, timeframe: str,
                   limit: int = 100, start: int = None, end: int = None) -> List[Dict]:
        """
        获取 K 线数据。

        读取顺序和写入顺序对应：优先读分表，分表为空时回退旧统一表。返回值统一按时间正序，
        这样上层图表、回测和同步统计不需要关心 SQLite 内部的 DESC 查询优化。
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        # 优先从分表读取，避免大统一表在历史数据很大时拖慢首屏或回测准备阶段。
        if timeframe in self._KLINE_SPLIT_TABLES:
            table = self._kline_table(timeframe)
            query = f'''
                SELECT timestamp, open, high, low, close, volume, quote_volume
                FROM {table}
                WHERE exchange = ? AND symbol = ?
            '''
            params: list = [exchange, symbol]
        else:
            query = '''
                SELECT timestamp, open, high, low, close, volume, quote_volume
                FROM kline_history
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            '''
            params = [exchange, symbol, timeframe]

        if start:
            query += ' AND timestamp >= ?'
            params.append(start)
        if end:
            query += ' AND timestamp <= ?'
            params.append(end)

        query += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows][::-1]  # SQL 为了 LIMIT 取最近数据用倒序，返回前再恢复正序。

        # 如果分表为空，回退到旧统一表。注意这不是 mock fallback，只读历史真实写入的数据。
        if not result and timeframe in self._KLINE_SPLIT_TABLES:
            query2 = '''
                SELECT timestamp, open, high, low, close, volume, quote_volume
                FROM kline_history
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            '''
            params2: list = [exchange, symbol, timeframe]
            if start:
                query2 += ' AND timestamp >= ?'
                params2.append(start)
            if end:
                query2 += ' AND timestamp <= ?'
                params2.append(end)
            query2 += ' ORDER BY timestamp DESC LIMIT ?'
            params2.append(limit)
            cursor.execute(query2, params2)
            rows2 = cursor.fetchall()
            result = [dict(row) for row in rows2][::-1]

        conn.close()
        return result

    # ============================================
    # 资金费率操作
    # ============================================

    def insert_funding_rate(self, exchange: str, symbol: str, timestamp: int,
                           rate: float, mark_price: float = None):
        """插入资金费率历史"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO funding_rate_history
            (exchange, symbol, timestamp, funding_rate, mark_price)
            VALUES (?, ?, ?, ?, ?)
        ''', (exchange, symbol, timestamp, rate, mark_price))

        conn.commit()
        conn.close()

    def get_funding_history(self, exchange: str, symbol: str, limit: int = 100) -> List[Dict]:
        """获取资金费率历史"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT timestamp, funding_rate as rate, mark_price
            FROM funding_rate_history
            WHERE exchange = ? AND symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (exchange, symbol, limit))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def update_funding_realtime(self, exchange: str, symbol: str, data: Dict):
        """更新资金费率实时数据"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO funding_rate_realtime
            (exchange, symbol, current_rate, predicted_rate, next_funding_time,
             mark_price, index_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (
            exchange, symbol,
            data.get('current_rate'),
            data.get('predicted_rate'),
            data.get('next_funding_time'),
            data.get('mark_price'),
            data.get('index_price')
        ))

        conn.commit()
        conn.close()

    def get_funding_realtime(self, exchange: str, symbol: str = None) -> List[Dict]:
        """获取资金费率实时数据"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if symbol:
            cursor.execute('''
                SELECT exchange, symbol, current_rate, predicted_rate,
                       next_funding_time, mark_price, index_price
                FROM funding_rate_realtime
                WHERE exchange = ? AND symbol = ?
            ''', (exchange, symbol))
        else:
            cursor.execute('''
                SELECT exchange, symbol, current_rate, predicted_rate,
                       next_funding_time, mark_price, index_price
                FROM funding_rate_realtime
                WHERE exchange = ?
                ORDER BY current_rate DESC
            ''', (exchange,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    # ============================================
    # 策略操作
    # ============================================

    def save_strategy(self, name: str, script_content: str, description: str = None,
                      config: Dict = None, exchange: str = None, symbols: List[str] = None) -> int:
        """保存策略定义；config/symbols 序列化为 JSON，便于 seed 导入和运行时恢复。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        config_json = json.dumps(config) if config else None
        symbols_json = json.dumps(symbols) if symbols else None

        cursor.execute('''
            INSERT OR REPLACE INTO strategies
            (name, description, script_content, config, exchange, symbols, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (name, description, script_content, config_json, exchange, symbols_json))

        strategy_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return strategy_id

    def get_strategies(self) -> List[Dict]:
        """获取所有策略，并把 JSON 字段还原为 Python 对象供 API/service 层直接使用。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, name, description, script_content, config, status,
                   exchange, symbols, run_started_at, created_at, updated_at
            FROM strategies
            ORDER BY updated_at DESC
        ''')

        rows = cursor.fetchall()
        conn.close()

        result = []
        for row in rows:
            item = dict(row)
            if item.get('config'):
                item['config'] = json.loads(item['config'])
            if item.get('symbols'):
                item['symbols'] = json.loads(item['symbols'])
            result.append(item)

        return result

    def get_strategy_by_id(self, strategy_id: int) -> Optional[Dict]:
        """根据 ID 获取策略；不存在时返回 None，让 API 层决定 404 或业务错误。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, name, description, script_content, config, status,
                   exchange, symbols, run_started_at, created_at, updated_at
            FROM strategies
            WHERE id = ?
        ''', (strategy_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        item = dict(row)
        if item.get('config'):
            item['config'] = json.loads(item['config'])
        if item.get('symbols'):
            item['symbols'] = json.loads(item['symbols'])

        return item

    def update_strategy_status(self, strategy_id: int, status: str, *, clear_run_started_at: bool = True):
        """更新策略状态；stopped/error 时清除持久化的运行起点（服务重启后运行时间用）"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if status in ("stopped", "error") and clear_run_started_at:
            cursor.execute(
                '''
                UPDATE strategies
                SET status = ?, run_started_at = NULL, updated_at = datetime('now')
                WHERE id = ?
                ''',
                (status, strategy_id),
            )
        else:
            cursor.execute(
                '''
                UPDATE strategies
                SET status = ?, updated_at = datetime('now')
                WHERE id = ?
                ''',
                (status, strategy_id),
            )

        conn.commit()
        conn.close()

    def clear_strategy_runtime_metrics(self, strategy_id: int) -> None:
        """清空某策略本轮运行指标：成交记录与持久化运行起点。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM strategy_trades WHERE strategy_id = ?', (strategy_id,))
        cursor.execute('DELETE FROM strategy_equity_samples WHERE strategy_id = ?', (strategy_id,))
        cursor.execute(
            '''
            UPDATE strategies
            SET run_started_at = NULL, updated_at = datetime('now')
            WHERE id = ?
            ''',
            (strategy_id,),
        )

        conn.commit()
        conn.close()

    def set_strategy_run_started_at(self, strategy_id: int, iso_utc: str) -> None:
        """记录策略本次连续运行起点（UTC ISO），用于进程重启后恢复仪表盘运行时间"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE strategies
            SET run_started_at = ?, updated_at = datetime('now')
            WHERE id = ?
            ''',
            (iso_utc, strategy_id),
        )
        conn.commit()
        conn.close()

    def update_strategy_config(self, strategy_id: int, config: Dict, symbols: Optional[List[str]] = None) -> bool:
        """更新策略配置；用于不改写策略代码/名称的运行参数调整。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if symbols is None:
            cursor.execute(
                '''
                UPDATE strategies
                SET config = ?, updated_at = datetime('now')
                WHERE id = ?
                ''',
                (json.dumps(config), strategy_id),
            )
        else:
            cursor.execute(
                '''
                UPDATE strategies
                SET config = ?, symbols = ?, updated_at = datetime('now')
                WHERE id = ?
                ''',
                (json.dumps(config), json.dumps(symbols), strategy_id),
            )

        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def delete_strategy(self, strategy_id: int) -> bool:
        """删除策略"""
        conn = self.get_connection()
        cursor = conn.cursor()

        def _table_exists(table_name: str) -> bool:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return cursor.fetchone() is not None

        # 兼容历史库：某些表可能尚未迁移创建，删除时不应直接报 500。
        # 这里不使用级联外键，是因为生产旧库的表结构可能不完整，显式清理更可控。
        if _table_exists('strategy_trades'):
            cursor.execute('DELETE FROM strategy_trades WHERE strategy_id = ?', (strategy_id,))
        if _table_exists('strategy_equity_samples'):
            cursor.execute('DELETE FROM strategy_equity_samples WHERE strategy_id = ?', (strategy_id,))
        if _table_exists('backtest_results'):
            cursor.execute('DELETE FROM backtest_results WHERE strategy_id = ?', (strategy_id,))

        # 删除策略主记录
        cursor.execute('DELETE FROM strategies WHERE id = ?', (strategy_id,))

        affected = cursor.rowcount
        conn.commit()
        conn.close()

        return affected > 0

    # ============================================
    # 策略交易记录操作
    # ============================================

    def insert_strategy_trade(self, strategy_id: int, trade: Dict):
        """插入策略交易记录；meta 保存执行细节，供监控、K 线复盘和诊断页面复原上下文。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO strategy_trades
            (strategy_id, exchange, symbol, order_id, timestamp, side, type,
             price, quantity, fee, fee_asset, pnl, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            strategy_id, trade['exchange'], trade['symbol'], trade.get('order_id'),
            trade['timestamp'], trade['side'], trade['type'],
            trade['price'], trade['quantity'],
            trade.get('fee'), trade.get('fee_asset'), trade.get('pnl'),
            json.dumps(trade.get('meta'), ensure_ascii=False) if trade.get('meta') is not None else None,
        ))

        conn.commit()
        conn.close()

    def get_strategy_trades(self, strategy_id: int, limit: int = 50) -> List[Dict]:
        """获取策略交易记录"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, strategy_id, exchange, symbol, order_id, timestamp,
                   side, type, price, quantity, fee, fee_asset, pnl, meta
            FROM strategy_trades
            WHERE strategy_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (strategy_id, limit))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def get_strategy_trades_since(self, strategy_id: int, since_ts_ms: int) -> List[Dict]:
        """按时间正序获取某次运行开始后的策略交易记录，用于恢复模拟盘持仓。"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, strategy_id, exchange, symbol, order_id, timestamp,
                   side, type, price, quantity, fee, fee_asset, pnl, meta
            FROM strategy_trades
            WHERE strategy_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC, id ASC
        ''', (strategy_id, since_ts_ms))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    # ============================================
    # 策略权益曲线采样操作
    # ============================================

    def insert_strategy_equity_sample(
        self,
        strategy_id: int,
        timestamp_ms: int,
        equity: float,
        *,
        balance: Optional[float] = None,
        realized_pnl: Optional[float] = None,
        unrealized_pnl: Optional[float] = None,
        total_pnl: Optional[float] = None,
        drawdown_pct: Optional[float] = None,
        return_pct: Optional[float] = None,
        win_rate: Optional[float] = None,
        profit_factor: Optional[float] = None,
        source: str = "runtime",
    ) -> bool:
        """写入策略权益曲线采样；无效权益不落库，避免暂停/停止误显示归零。"""
        try:
            sid = int(strategy_id)
            ts = int(timestamp_ms)
            eq = float(equity)
        except (TypeError, ValueError):
            return False
        # 权益曲线是页面 KPI 和重启恢复的重要来源。0 或负权益通常来自暂停/停止边界或异常快照，
        # 直接落库会让监控误判为账户归零，所以这里宁可丢弃。
        if sid <= 0 or ts <= 0 or eq <= 0:
            return False

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO strategy_equity_samples
            (strategy_id, timestamp, equity, balance, realized_pnl, unrealized_pnl,
             total_pnl, drawdown_pct, return_pct, win_rate, profit_factor, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id, timestamp) DO UPDATE SET
                equity = excluded.equity,
                balance = excluded.balance,
                realized_pnl = excluded.realized_pnl,
                unrealized_pnl = excluded.unrealized_pnl,
                total_pnl = excluded.total_pnl,
                drawdown_pct = excluded.drawdown_pct,
                return_pct = excluded.return_pct,
                win_rate = excluded.win_rate,
                profit_factor = excluded.profit_factor,
                source = excluded.source,
                created_at = excluded.created_at
            ''',
            (
                sid,
                ts,
                eq,
                balance,
                realized_pnl,
                unrealized_pnl,
                total_pnl,
                drawdown_pct,
                return_pct,
                win_rate,
                profit_factor,
                source,
                datetime.utcnow().isoformat() + "Z",
            ),
        )
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def _format_equity_sample(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        ts = int(row["timestamp"])
        out = {
            "timestamp": ts,
            "equity": float(row["equity"]),
            "time": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
        }
        keys = set(row.keys()) if hasattr(row, "keys") else set(row)
        for key in ("total_pnl", "return_pct", "win_rate", "profit_factor"):
            if key not in keys:
                continue
            value = row[key]
            if value is not None:
                out[key] = float(value)
        return out

    def get_strategy_equity_samples(
        self,
        strategy_id: int,
        limit: int = 400,
        since_ts_ms: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """按时间正序获取策略权益曲线采样，默认返回最近 400 个点。"""
        sid = int(strategy_id)
        safe_limit = max(1, min(int(limit or 400), 5000))
        conn = self.get_connection()
        cursor = conn.cursor()
        if since_ts_ms is None:
            cursor.execute(
                '''
                SELECT timestamp, equity, total_pnl, return_pct, win_rate, profit_factor
                FROM (
                    SELECT timestamp, equity, total_pnl, return_pct, win_rate, profit_factor
                    FROM strategy_equity_samples
                    WHERE strategy_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                ) t
                ORDER BY timestamp ASC
                ''',
                (sid, safe_limit),
            )
        else:
            cursor.execute(
                '''
                SELECT timestamp, equity, total_pnl, return_pct, win_rate, profit_factor
                FROM strategy_equity_samples
                WHERE strategy_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
                LIMIT ?
                ''',
                (sid, int(since_ts_ms), safe_limit),
            )
        rows = cursor.fetchall()
        conn.close()
        return [self._format_equity_sample(row) for row in rows]

    def get_latest_strategy_equity_sample(self, strategy_id: int) -> Optional[Dict[str, Any]]:
        """获取某策略最近一个有效权益采样点。"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT timestamp, equity
            FROM strategy_equity_samples
            WHERE strategy_id = ? AND equity > 0
            ORDER BY timestamp DESC
            LIMIT 1
            ''',
            (int(strategy_id),),
        )
        row = cursor.fetchone()
        conn.close()
        return self._format_equity_sample(row) if row else None

    # ============================================
    # Ticker 缓存操作
    # ============================================

    def update_ticker_cache(self, exchange: str, symbol: str, ticker: Dict):
        """更新 Ticker 缓存"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT OR REPLACE INTO ticker_cache
            (exchange, symbol, last, bid, ask, high, low, volume, quote_volume,
             change, change_percent, timestamp, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ''', (
            exchange, symbol,
            ticker.get('last'), ticker.get('bid'), ticker.get('ask'),
            ticker.get('high'), ticker.get('low'), ticker.get('volume'),
            ticker.get('quote_volume'), ticker.get('change'),
            ticker.get('change_percent'), ticker.get('timestamp')
        ))

        conn.commit()
        conn.close()

    def get_ticker_cache(self, exchange: str, symbol: str = None) -> List[Dict]:
        """获取 Ticker 缓存"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if symbol:
            cursor.execute('''
                SELECT exchange, symbol, last, bid, ask, high, low, volume,
                       quote_volume, change, change_percent, timestamp
                FROM ticker_cache
                WHERE exchange = ? AND symbol = ?
            ''', (exchange, symbol))
        else:
            cursor.execute('''
                SELECT exchange, symbol, last, bid, ask, high, low, volume,
                       quote_volume, change, change_percent, timestamp
                FROM ticker_cache
                WHERE exchange = ?
            ''', (exchange,))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]


    # ============================================
    # 同步元数据操作
    # ============================================

    def get_sync_metadata(self, exchange: str, symbol: str, timeframe: str,
                          data_type: str = 'kline') -> Optional[Dict]:
        """获取同步元数据"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT exchange, symbol, timeframe, data_type,
                   first_timestamp, last_timestamp, total_records,
                   status, last_sync_at, error_message
            FROM sync_metadata
            WHERE exchange = ? AND symbol = ? AND timeframe = ? AND data_type = ?
        ''', (exchange, symbol, timeframe, data_type))

        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

    def update_sync_metadata(self, exchange: str, symbol: str, timeframe: str,
                             data_type: str = 'kline', **kwargs):
        """更新同步元数据"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # 先尝试获取已有记录
        cursor.execute('''
            SELECT id FROM sync_metadata
            WHERE exchange = ? AND symbol = ? AND timeframe = ? AND data_type = ?
        ''', (exchange, symbol, timeframe, data_type))

        row = cursor.fetchone()

        if row:
            # 构建动态 UPDATE
            set_clauses = ['updated_at = datetime("now")']
            params = []
            for key in ['first_timestamp', 'last_timestamp', 'total_records',
                        'status', 'last_sync_at', 'error_message']:
                if key in kwargs:
                    set_clauses.append(f'{key} = ?')
                    params.append(kwargs[key])

            params.extend([exchange, symbol, timeframe, data_type])
            cursor.execute(f'''
                UPDATE sync_metadata
                SET {", ".join(set_clauses)}
                WHERE exchange = ? AND symbol = ? AND timeframe = ? AND data_type = ?
            ''', params)
        else:
            # INSERT
            cursor.execute('''
                INSERT INTO sync_metadata
                (exchange, symbol, timeframe, data_type, first_timestamp, last_timestamp,
                 total_records, status, last_sync_at, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                exchange, symbol, timeframe, data_type,
                kwargs.get('first_timestamp'),
                kwargs.get('last_timestamp'),
                kwargs.get('total_records', 0),
                kwargs.get('status', 'idle'),
                kwargs.get('last_sync_at'),
                kwargs.get('error_message')
            ))

        conn.commit()
        conn.close()

    def get_all_sync_metadata(self, exchange: str = None) -> List[Dict]:
        """获取所有同步元数据"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if exchange:
            cursor.execute('''
                SELECT exchange, symbol, timeframe, data_type,
                       first_timestamp, last_timestamp, total_records,
                       status, last_sync_at, error_message, updated_at
                FROM sync_metadata
                WHERE exchange = ?
                ORDER BY symbol, timeframe
            ''', (exchange,))
        else:
            cursor.execute('''
                SELECT exchange, symbol, timeframe, data_type,
                       first_timestamp, last_timestamp, total_records,
                       status, last_sync_at, error_message, updated_at
                FROM sync_metadata
                ORDER BY exchange, symbol, timeframe
            ''')

        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_kline_count(self, exchange: str, symbol: str, timeframe: str) -> int:
        """获取K线数据条数 — 优先查分表"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if timeframe in self._KLINE_SPLIT_TABLES:
            table = self._kline_table(timeframe)
            cursor.execute(f'''
                SELECT COUNT(*) as cnt
                FROM {table}
                WHERE exchange = ? AND symbol = ?
            ''', (exchange, symbol))
        else:
            cursor.execute('''
                SELECT COUNT(*) as cnt
                FROM kline_history
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            ''', (exchange, symbol, timeframe))

        row = cursor.fetchone()
        cnt = row['cnt'] if row else 0

        # 如果分表为 0，尝试旧统一表
        if cnt == 0 and timeframe in self._KLINE_SPLIT_TABLES:
            cursor.execute('''
                SELECT COUNT(*) as cnt
                FROM kline_history
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            ''', (exchange, symbol, timeframe))
            row2 = cursor.fetchone()
            cnt = row2['cnt'] if row2 else 0

        conn.close()
        return cnt

    def get_kline_time_range(self, exchange: str, symbol: str, timeframe: str) -> Optional[Dict]:
        """获取K线数据的时间范围 — 优先查分表"""
        conn = self.get_connection()
        cursor = conn.cursor()

        if timeframe in self._KLINE_SPLIT_TABLES:
            table = self._kline_table(timeframe)
            cursor.execute(f'''
                SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts, COUNT(*) as cnt
                FROM {table}
                WHERE exchange = ? AND symbol = ?
            ''', (exchange, symbol))
        else:
            cursor.execute('''
                SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts, COUNT(*) as cnt
                FROM kline_history
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            ''', (exchange, symbol, timeframe))

        row = cursor.fetchone()

        # 如果分表没数据，回退旧表
        if (not row or row['cnt'] == 0) and timeframe in self._KLINE_SPLIT_TABLES:
            cursor.execute('''
                SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts, COUNT(*) as cnt
                FROM kline_history
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
            ''', (exchange, symbol, timeframe))
            row = cursor.fetchone()

        conn.close()

        if row and row['cnt'] > 0:
            return {
                'first_timestamp': row['first_ts'],
                'last_timestamp': row['last_ts'],
                'count': row['cnt']
            }
        return None

    def get_kline_table_stats(self) -> List[Dict]:
        """获取所有分表的统计信息（供前端数据管理页面使用）"""
        conn = self.get_connection()
        cursor = conn.cursor()
        result = []

        for tf in sorted(self._KLINE_SPLIT_TABLES):
            table = self._kline_table(tf)
            try:
                cursor.execute(f'''
                    SELECT exchange, symbol,
                           COUNT(*) as record_count,
                           MIN(timestamp) as first_ts,
                           MAX(timestamp) as last_ts
                    FROM {table}
                    GROUP BY exchange, symbol
                    ORDER BY exchange, symbol
                ''')
                for row in cursor.fetchall():
                    result.append({
                        'table_name': table,
                        'timeframe': tf,
                        'exchange': row['exchange'],
                        'symbol': row['symbol'],
                        'record_count': row['record_count'],
                        'first_timestamp': row['first_ts'],
                        'last_timestamp': row['last_ts'],
                    })
            except Exception:
                pass

        # 旧统一表统计
        try:
            cursor.execute('''
                SELECT exchange, symbol, timeframe,
                       COUNT(*) as record_count,
                       MIN(timestamp) as first_ts,
                       MAX(timestamp) as last_ts
                FROM kline_history
                GROUP BY exchange, symbol, timeframe
                ORDER BY exchange, symbol, timeframe
            ''')
            for row in cursor.fetchall():
                result.append({
                    'table_name': 'kline_history',
                    'timeframe': row['timeframe'],
                    'exchange': row['exchange'],
                    'symbol': row['symbol'],
                    'record_count': row['record_count'],
                    'first_timestamp': row['first_ts'],
                    'last_timestamp': row['last_ts'],
                })
        except Exception:
            pass

        conn.close()
        return result

    # ============================================
    # Agent 任务持久化
    # ============================================

    def save_agent_task(self, task_data: dict):
        """保存或更新 Agent 任务"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO agent_tasks
            (id, status, stage, stage_label, goal_criteria, market_type, symbol, timeframe,
             backtest_start, backtest_end, max_iterations,
             current_iteration, best_iteration, user_prompt,
             llm_model, strategy_spec, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                stage = excluded.stage,
                stage_label = excluded.stage_label,
                goal_criteria = excluded.goal_criteria,
                market_type = excluded.market_type,
                symbol = excluded.symbol,
                timeframe = excluded.timeframe,
                backtest_start = excluded.backtest_start,
                backtest_end = excluded.backtest_end,
                max_iterations = excluded.max_iterations,
                current_iteration = excluded.current_iteration,
                best_iteration = excluded.best_iteration,
                user_prompt = excluded.user_prompt,
                llm_model = excluded.llm_model,
                strategy_spec = excluded.strategy_spec,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
        ''', (
            task_data['id'], task_data['status'],
            task_data.get('stage', ''),
            task_data.get('stage_label', ''),
            json.dumps(task_data.get('goal_criteria', {})),
            task_data.get('market_type', 'spot'),
            task_data['symbol'], task_data['timeframe'],
            task_data['backtest_start'], task_data['backtest_end'],
            task_data.get('max_iterations', 10),
            task_data.get('current_iteration', 0),
            task_data.get('best_iteration'),
            task_data.get('user_prompt', ''),
            str(task_data.get('llm_model') or ''),
            json.dumps(task_data.get('strategy_spec')) if task_data.get('strategy_spec') is not None else None,
            task_data['created_at'], task_data['updated_at'],
        ))
        conn.commit()

    def update_agent_task_status(self, task_id: str, status: str, updated_at: str = None) -> bool:
        """只更新 Agent 任务状态，保留已有迭代记录"""
        conn = self.get_connection()
        cursor = conn.cursor()
        labels = {
            "stopped": "任务已停止",
            "failed": "已失败",
            "completed": "研发任务已完成",
            "interrupted": "服务重启，任务已中断，可从已保存迭代继续研发",
        }
        cursor.execute(
            'UPDATE agent_tasks SET status = ?, stage = ?, stage_label = ?, updated_at = ? WHERE id = ?',
            (
                status,
                status,
                labels.get(status, status),
                updated_at or datetime.now().isoformat(),
                task_id,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0

    def save_agent_iteration(self, task_id: str, record: dict):
        """保存一条迭代记录"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT id FROM agent_iterations
            WHERE task_id = ? AND iteration = ?
            ORDER BY id DESC LIMIT 1
            ''',
            (task_id, record['iteration']),
        )
        existing = cursor.fetchone()
        payload = (
            record.get('strategy_name', ''),
            record.get('strategy_code', ''),
            record.get('reasoning', ''),
            json.dumps(record.get('backtest_metrics', {})),
            record.get('analysis', ''),
            json.dumps(record.get('suggestions', [])),
            json.dumps(record.get('eval_scores')) if record.get('eval_scores') is not None else None,
            json.dumps(record.get('contract')) if record.get('contract') is not None else None,
            record.get('action', 'new'),
            record.get('score', 0),
            1 if record.get('meets_goal') else 0,
            record.get('error', ''),
            record.get('created_at', ''),
        )
        if existing:
            cursor.execute(
                '''
                UPDATE agent_iterations
                SET strategy_name = ?, strategy_code = ?, reasoning = ?,
                    backtest_metrics = ?, analysis = ?, suggestions = ?,
                    eval_scores = ?, contract = ?, action = ?,
                    score = ?, meets_goal = ?, error = ?, created_at = ?
                WHERE id = ?
                ''',
                (*payload, existing['id']),
            )
            conn.commit()
            return

        cursor.execute('''
            INSERT INTO agent_iterations
            (task_id, iteration, strategy_name, strategy_code,
             reasoning, backtest_metrics, analysis, suggestions,
             eval_scores, contract, action,
             score, meets_goal, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            task_id, record['iteration'],
            *payload,
        ))
        conn.commit()

    def get_agent_tasks(self, limit: int = 50) -> list:
        """获取 Agent 任务列表"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM agent_tasks ORDER BY created_at DESC LIMIT ?',
            (limit,),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get('goal_criteria'):
                d['goal_criteria'] = json.loads(d['goal_criteria'])
            if d.get('strategy_spec'):
                d['strategy_spec'] = json.loads(d['strategy_spec'])
            result.append(d)
        return result

    def get_interrupted_agent_tasks(self, updated_at: str = None, limit: int = 50) -> list:
        """获取可恢复的 interrupted Agent 任务，可按本次重启时间戳精确筛选。"""
        conn = self.get_connection()
        cursor = conn.cursor()
        if updated_at:
            cursor.execute(
                '''
                SELECT * FROM agent_tasks
                WHERE status = 'interrupted' AND updated_at = ?
                ORDER BY created_at DESC LIMIT ?
                ''',
                (updated_at, limit),
            )
        else:
            cursor.execute(
                '''
                SELECT * FROM agent_tasks
                WHERE status = 'interrupted'
                ORDER BY updated_at DESC LIMIT ?
                ''',
                (limit,),
            )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get('goal_criteria'):
                d['goal_criteria'] = json.loads(d['goal_criteria'])
            if d.get('strategy_spec'):
                d['strategy_spec'] = json.loads(d['strategy_spec'])
            result.append(d)
        return result

    def get_agent_task(self, task_id: str) -> Optional[dict]:
        """获取单个 Agent 任务"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM agent_tasks WHERE id = ?', (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get('goal_criteria'):
            d['goal_criteria'] = json.loads(d['goal_criteria'])
        if d.get('strategy_spec'):
            d['strategy_spec'] = json.loads(d['strategy_spec'])
        return d

    def get_agent_iterations(self, task_id: str) -> list:
        """获取某任务的所有迭代记录"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM agent_iterations WHERE task_id = ? ORDER BY iteration',
            (task_id,),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get('backtest_metrics'):
                d['backtest_metrics'] = json.loads(d['backtest_metrics'])
            if d.get('suggestions'):
                d['suggestions'] = json.loads(d['suggestions'])
            if d.get('eval_scores'):
                d['eval_scores'] = json.loads(d['eval_scores'])
            if d.get('contract'):
                d['contract'] = json.loads(d['contract'])
            if 'meets_goal' in d:
                d['meets_goal'] = bool(d.get('meets_goal'))
            result.append(d)
        return result

    def delete_agent_task(self, task_id: str) -> dict:
        """删除 Agent 任务及其迭代记录。"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM agent_iterations WHERE task_id = ?', (task_id,))
        iterations_deleted = cursor.rowcount
        cursor.execute('DELETE FROM agent_tasks WHERE id = ?', (task_id,))
        tasks_deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return {
            "task_deleted": int(tasks_deleted),
            "iterations_deleted": int(iterations_deleted),
        }

    def mark_interrupted_agent_tasks(self, updated_at: str = None) -> int:
        """Mark in-flight Agent tasks as interrupted after a process restart."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE agent_tasks
            SET status = 'interrupted',
                stage = 'interrupted',
                stage_label = '服务重启，任务已中断，可从已保存迭代继续研发',
                updated_at = ?
            WHERE status IN ('pending', 'running')
            ''',
            (updated_at or datetime.now().isoformat(),),
        )
        conn.commit()
        return cursor.rowcount

    # ============================================
    # 监控中心运行策略收益卡片推送
    # ============================================

    def get_app_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            '''
        )
        cursor.execute('SELECT value FROM app_settings WHERE setting_key = ?', (key,))
        row = cursor.fetchone()
        return str(row['value']) if row and row['value'] is not None else default

    def set_app_setting(self, key: str, value: Optional[str]) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
            '''
        )
        cursor.execute(
            '''
            INSERT INTO app_settings (setting_key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            ''',
            (key, value, datetime.now().isoformat()),
        )
        conn.commit()

    def get_feishu_webhook_url(self) -> Optional[str]:
        url = str(self.get_app_setting('feishu_webhook_url', '') or '').strip()
        return url if 'open-apis/bot' in url else None

    def set_feishu_webhook_url(self, url: str) -> None:
        self.set_app_setting('feishu_webhook_url', str(url or '').strip() or None)

    def clear_monitor_profit_push_error(self) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE monitor_profit_push_config
            SET last_error = NULL,
                last_skip_reason = NULL,
                updated_at = ?
            WHERE id = 1
            ''',
            (datetime.now().isoformat(),),
        )
        conn.commit()

    def clear_live_profit_push_error(self) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            UPDATE live_profit_push_config
            SET last_error = NULL,
                last_skip_reason = NULL,
                updated_at = ?
            WHERE id = 1
            ''',
            (datetime.now().isoformat(),),
        )
        conn.commit()

    def _get_profit_push_config_from_table(self, table_name: str) -> Dict[str, Any]:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(f'''
            INSERT OR IGNORE INTO {table_name}
            (id, enabled, interval_minutes, running, updated_at)
            VALUES (1, 0, 60, 0, ?)
        ''', (now,))
        cursor.execute(f'SELECT * FROM {table_name} WHERE id = 1')
        row = cursor.fetchone()
        conn.commit()
        data = dict(row or {})
        data['enabled'] = bool(data.get('enabled'))
        data['running'] = bool(data.get('running'))
        data['interval_minutes'] = int(data.get('interval_minutes') or 60)
        return data

    def _update_profit_push_config_table(self, table_name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {'enabled', 'interval_minutes'}
        values = {k: v for k, v in updates.items() if k in allowed and v is not None}
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(f'''
            INSERT OR IGNORE INTO {table_name}
            (id, enabled, interval_minutes, running, updated_at)
            VALUES (1, 0, 60, 0, ?)
        ''', (now,))
        if values:
            assignments = []
            params: List[Any] = []
            for key, value in values.items():
                assignments.append(f"{key} = ?")
                if key == 'enabled':
                    params.append(1 if bool(value) else 0)
                else:
                    try:
                        interval = int(float(value))
                    except (TypeError, ValueError):
                        interval = 60
                    interval = max(1, min(interval, 24 * 60))
                    params.append(interval)
            assignments.append("updated_at = ?")
            params.append(now)
            params.append(1)
            cursor.execute(
                f"UPDATE {table_name} SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
        conn.commit()
        return self._get_profit_push_config_from_table(table_name)

    def _set_profit_push_runtime_table(
        self,
        table_name: str,
        *,
        running: bool,
        last_started_at: str = None,
        last_sent_at: str = None,
        last_finished_at: str = None,
        last_error: str = None,
        last_skip_reason: str = None,
    ) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute(f'''
            INSERT OR IGNORE INTO {table_name}
            (id, enabled, interval_minutes, running, updated_at)
            VALUES (1, 0, 60, 0, ?)
        ''', (now,))
        cursor.execute(
            f'''
            UPDATE {table_name}
            SET running = ?,
                last_started_at = COALESCE(?, last_started_at),
                last_sent_at = COALESCE(?, last_sent_at),
                last_finished_at = COALESCE(?, last_finished_at),
                last_error = ?,
                last_skip_reason = ?,
                updated_at = ?
            WHERE id = 1
            ''',
            (
                1 if running else 0,
                last_started_at,
                last_sent_at,
                last_finished_at,
                last_error,
                last_skip_reason,
                now,
            ),
        )
        conn.commit()

    def get_monitor_profit_push_config(self) -> Dict[str, Any]:
        return self._get_profit_push_config_from_table('monitor_profit_push_config')

    def update_monitor_profit_push_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        return self._update_profit_push_config_table('monitor_profit_push_config', updates)

    def get_live_profit_push_config(self) -> Dict[str, Any]:
        return self._get_profit_push_config_from_table('live_profit_push_config')

    def update_live_profit_push_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        return self._update_profit_push_config_table('live_profit_push_config', updates)

    def get_latest_feishu_webhook_url(self) -> Optional[str]:
        """Return the latest legacy Feishu webhook saved in alert config."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT notification
            FROM alerts
            WHERE notification IS NOT NULL AND notification != ''
            ORDER BY id DESC
        ''')
        for row in cursor.fetchall():
            try:
                notification = json.loads(row['notification'] or '{}')
            except Exception:
                continue
            webhook = notification.get('webhook') if isinstance(notification, dict) else None
            if not isinstance(webhook, dict):
                continue
            url = str(webhook.get('url') or '').strip()
            if 'open-apis/bot' in url:
                return url
        return None

    def set_monitor_profit_push_runtime(
        self,
        *,
        running: bool,
        last_started_at: str = None,
        last_sent_at: str = None,
        last_finished_at: str = None,
        last_error: str = None,
        last_skip_reason: str = None,
    ) -> None:
        self._set_profit_push_runtime_table(
            'monitor_profit_push_config',
            running=running,
            last_started_at=last_started_at,
            last_sent_at=last_sent_at,
            last_finished_at=last_finished_at,
            last_error=last_error,
            last_skip_reason=last_skip_reason,
        )

    def set_live_profit_push_runtime(
        self,
        *,
        running: bool,
        last_started_at: str = None,
        last_sent_at: str = None,
        last_finished_at: str = None,
        last_error: str = None,
        last_skip_reason: str = None,
    ) -> None:
        self._set_profit_push_runtime_table(
            'live_profit_push_config',
            running=running,
            last_started_at=last_started_at,
            last_sent_at=last_sent_at,
            last_finished_at=last_finished_at,
            last_error=last_error,
            last_skip_reason=last_skip_reason,
        )

    # ============================================
    # AI Lab 现有策略自动优化
    # ============================================

    @staticmethod
    def _decode_json_field(value: Any, default: Any = None) -> Any:
        if value is None or value == "":
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception:
            return default

    def get_strategy_optimizer_config(self) -> Dict[str, Any]:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR IGNORE INTO strategy_optimizer_config
            (id, enabled, interval_hours, low_return_pct, trial_hours, trial_success_return_pct, running, updated_at)
            VALUES (1, 0, 4, 0, 4, 0, 0, ?)
        ''', (now,))
        cursor.execute('SELECT * FROM strategy_optimizer_config WHERE id = 1')
        row = cursor.fetchone()
        conn.commit()
        data = dict(row or {})
        data['enabled'] = bool(data.get('enabled'))
        data['running'] = bool(data.get('running'))
        return data

    def update_strategy_optimizer_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            'enabled', 'interval_hours', 'low_return_pct',
            'trial_hours', 'trial_success_return_pct', 'llm_model',
        }
        values = {k: v for k, v in updates.items() if k in allowed and v is not None}
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR IGNORE INTO strategy_optimizer_config
            (id, enabled, interval_hours, low_return_pct, trial_hours, trial_success_return_pct, running, updated_at)
            VALUES (1, 0, 4, 0, 4, 0, 0, ?)
        ''', (now,))
        if values:
            assignments = []
            params: List[Any] = []
            for key, value in values.items():
                assignments.append(f"{key} = ?")
                if key == 'enabled':
                    params.append(1 if bool(value) else 0)
                elif key == 'llm_model':
                    params.append(str(value).strip())
                else:
                    params.append(float(value))
            assignments.append("updated_at = ?")
            params.append(now)
            params.append(1)
            cursor.execute(
                f"UPDATE strategy_optimizer_config SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
        conn.commit()
        return self.get_strategy_optimizer_config()

    def set_strategy_optimizer_runtime(
        self,
        *,
        running: bool,
        last_started_at: str = None,
        last_finished_at: str = None,
        last_error: str = None,
    ) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR IGNORE INTO strategy_optimizer_config
            (id, enabled, interval_hours, low_return_pct, trial_hours, trial_success_return_pct, running, updated_at)
            VALUES (1, 0, 4, 0, 4, 0, 0, ?)
        ''', (now,))
        cursor.execute(
            '''
            UPDATE strategy_optimizer_config
            SET running = ?,
                last_started_at = COALESCE(?, last_started_at),
                last_finished_at = COALESCE(?, last_finished_at),
                last_error = ?,
                updated_at = ?
            WHERE id = 1
            ''',
            (1 if running else 0, last_started_at, last_finished_at, last_error, now),
        )
        conn.commit()

    def save_strategy_optimization_run(self, run: Dict[str, Any]) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        payload = dict(run)
        now = datetime.now().isoformat()
        payload.setdefault('created_at', now)
        payload.setdefault('updated_at', now)
        for key in ('source_snapshot', 'backtest_result'):
            if isinstance(payload.get(key), (dict, list)):
                payload[key] = json.dumps(payload[key], ensure_ascii=False)
        cursor.execute(
            '''
            INSERT INTO strategy_optimization_runs
            (id, source_strategy_id, source_strategy_name, candidate_strategy_id,
             agent_task_id, stage, status, source_return_pct, candidate_return_pct,
             source_snapshot, ai_analysis, backtest_result, trial_started_at,
             trial_checked_at, trial_finished_at, error_message, created_at, updated_at)
            VALUES
            (:id, :source_strategy_id, :source_strategy_name, :candidate_strategy_id,
             :agent_task_id, :stage, :status, :source_return_pct, :candidate_return_pct,
             :source_snapshot, :ai_analysis, :backtest_result, :trial_started_at,
             :trial_checked_at, :trial_finished_at, :error_message, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                source_strategy_name = excluded.source_strategy_name,
                candidate_strategy_id = excluded.candidate_strategy_id,
                agent_task_id = excluded.agent_task_id,
                stage = excluded.stage,
                status = excluded.status,
                source_return_pct = excluded.source_return_pct,
                candidate_return_pct = excluded.candidate_return_pct,
                source_snapshot = excluded.source_snapshot,
                ai_analysis = excluded.ai_analysis,
                backtest_result = excluded.backtest_result,
                trial_started_at = excluded.trial_started_at,
                trial_checked_at = excluded.trial_checked_at,
                trial_finished_at = excluded.trial_finished_at,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            ''',
            {
                'id': payload.get('id'),
                'source_strategy_id': payload.get('source_strategy_id'),
                'source_strategy_name': payload.get('source_strategy_name'),
                'candidate_strategy_id': payload.get('candidate_strategy_id'),
                'agent_task_id': payload.get('agent_task_id'),
                'stage': payload.get('stage') or 'monitor',
                'status': payload.get('status') or 'running',
                'source_return_pct': payload.get('source_return_pct'),
                'candidate_return_pct': payload.get('candidate_return_pct'),
                'source_snapshot': payload.get('source_snapshot'),
                'ai_analysis': payload.get('ai_analysis'),
                'backtest_result': payload.get('backtest_result'),
                'trial_started_at': payload.get('trial_started_at'),
                'trial_checked_at': payload.get('trial_checked_at'),
                'trial_finished_at': payload.get('trial_finished_at'),
                'error_message': payload.get('error_message'),
                'created_at': payload.get('created_at'),
                'updated_at': payload.get('updated_at') or now,
            },
        )
        conn.commit()

    def get_strategy_optimization_runs(self, limit: int = 50, lightweight: bool = False) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        if lightweight:
            cursor.execute(
                '''
                SELECT id, source_strategy_id, source_strategy_name, candidate_strategy_id,
                       agent_task_id, stage, status, source_return_pct, candidate_return_pct,
                       NULL AS source_snapshot, NULL AS ai_analysis, NULL AS backtest_result,
                       trial_started_at, trial_checked_at, trial_finished_at, error_message,
                       created_at, updated_at
                FROM strategy_optimization_runs
                ORDER BY created_at DESC
                LIMIT ?
                ''',
                (limit,),
            )
        else:
            cursor.execute(
                'SELECT * FROM strategy_optimization_runs ORDER BY created_at DESC LIMIT ?',
                (limit,),
            )
        rows = cursor.fetchall()
        conn.close()
        return [self._strategy_optimization_run_from_row(row) for row in rows]

    def get_strategy_optimization_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM strategy_optimization_runs WHERE id = ?', (run_id,))
        row = cursor.fetchone()
        return self._strategy_optimization_run_from_row(row) if row else None

    def delete_strategy_optimization_run(self, run_id: str) -> Dict[str, int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM strategy_optimization_events WHERE run_id = ?', (run_id,))
        events_deleted = cursor.rowcount
        cursor.execute('DELETE FROM strategy_optimization_runs WHERE id = ?', (run_id,))
        run_deleted = cursor.rowcount
        conn.commit()
        return {"run_deleted": run_deleted, "events_deleted": events_deleted}

    def get_active_strategy_optimization_runs(self, source_strategy_id: int = None) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        statuses = ('running', 'trial_running')
        if source_strategy_id is None:
            cursor.execute(
                '''
                SELECT * FROM strategy_optimization_runs
                WHERE status IN (?, ?)
                ORDER BY created_at DESC
                ''',
                statuses,
            )
        else:
            cursor.execute(
                '''
                SELECT * FROM strategy_optimization_runs
                WHERE source_strategy_id = ? AND status IN (?, ?)
                ORDER BY created_at DESC
                ''',
                (source_strategy_id, *statuses),
            )
        return [self._strategy_optimization_run_from_row(row) for row in cursor.fetchall()]

    def add_strategy_optimization_event(
        self,
        run_id: str,
        stage: str,
        message: str,
        detail: Dict[str, Any] = None,
        ts: str = None,
    ) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO strategy_optimization_events (run_id, ts, stage, message, detail)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (
                run_id,
                ts or datetime.now().isoformat(),
                stage,
                message,
                json.dumps(detail or {}, ensure_ascii=False),
            ),
        )
        conn.commit()

    def get_strategy_optimization_events(self, run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT * FROM strategy_optimization_events
            WHERE run_id = ?
            ORDER BY id ASC
            LIMIT ?
            ''',
            (run_id, limit),
        )
        events = []
        for row in cursor.fetchall():
            item = dict(row)
            item['detail'] = self._decode_json_field(item.get('detail'), {})
            events.append(item)
        return events

    def _strategy_optimization_run_from_row(self, row: Any) -> Dict[str, Any]:
        item = dict(row)
        item['source_snapshot'] = self._decode_json_field(item.get('source_snapshot'), {})
        item['backtest_result'] = self._decode_json_field(item.get('backtest_result'), {})
        return item

    # ============================================
    # AI 预测 K 线持久化
    # ============================================

    def insert_ai_predictions(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        bars: List[Dict],
        predicted_at_ms: int,
    ) -> int:
        """
        写入一批预测 K 线。同一 (exchange,symbol,timeframe,target_timestamp) 可有多条记录
        （不同 predicted_at），复盘时按时间窗口去重选取。
        """
        if not bars:
            return 0
        conn = self.get_connection()
        cursor = conn.cursor()
        rows = []
        for b in bars:
            ts = int(b.get("timestamp", 0))
            if not ts:
                continue
            vol = b.get("volume")
            qv = b.get("quote_volume")
            vol_sql: Optional[float] = None
            qv_sql: Optional[float] = None
            if vol is not None:
                try:
                    fv = float(vol)
                    if fv > 0:
                        vol_sql = fv
                except (TypeError, ValueError):
                    pass
            if qv is not None:
                try:
                    fq = float(qv)
                    if fq > 0:
                        qv_sql = fq
                except (TypeError, ValueError):
                    pass
            rows.append(
                (
                    exchange,
                    symbol,
                    timeframe,
                    ts,
                    float(b["open"]),
                    float(b["high"]),
                    float(b["low"]),
                    float(b["close"]),
                    vol_sql,
                    qv_sql,
                    int(predicted_at_ms),
                )
            )
        if not rows:
            conn.close()
            return 0
        cursor.executemany(
            """
            INSERT INTO ai_predictions
            (exchange, symbol, timeframe, target_timestamp, open, high, low, close, volume, quote_volume, predicted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()
        return len(rows)

    def get_ai_predictions_deduped_by_target(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
    ) -> List[Dict]:
        """
        查询 [start_ts, end_ts] 内每个 target_timestamp 的一条预测记录。
        若同一目标时间多次预测，取 predicted_at 最新的一条（与复盘「最近一次观点」一致）。
        依赖 SQLite 窗口函数（3.25+）。
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, exchange, symbol, timeframe, target_timestamp,
                   open, high, low, close, volume, quote_volume, predicted_at
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY target_timestamp
                           ORDER BY predicted_at DESC
                       ) AS rn
                FROM ai_predictions
                WHERE exchange = ? AND symbol = ? AND timeframe = ?
                  AND target_timestamp >= ? AND target_timestamp <= ?
            ) t
            WHERE rn = 1
            ORDER BY target_timestamp ASC
            """,
            (exchange, symbol, timeframe, start_ts, end_ts),
        )
        result = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return result


# 全局数据库实例
db_instance = LocalDatabase()
