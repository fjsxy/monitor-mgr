// SQLite按连续版本迁移；升级前保留一致性快照，失败不能替换为空库。
package control

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// schemaVersion 定义本程序能够读取和升级到的数据库版本。
const schemaVersion = 11

// migrationSQL 按版本依次保存结构变更，不修改已经发布的旧版本语句。
var migrationSQL = [][]string{
	{`CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)`,
		`CREATE TABLE accounts(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash BLOB NOT NULL,role TEXT NOT NULL CHECK(role IN ('ADMIN','USER')),enabled INTEGER NOT NULL,must_change INTEGER NOT NULL,auth_version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)`,
		`CREATE TABLE sessions(digest TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),auth_version INTEGER NOT NULL,expires_at TEXT NOT NULL)`,
		`CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT,action TEXT NOT NULL,target_id TEXT,created_at TEXT NOT NULL)`},
	{`CREATE TABLE command_results(actor_id TEXT NOT NULL,action TEXT NOT NULL,target_id TEXT NOT NULL,command_key TEXT NOT NULL,request_digest TEXT NOT NULL,result_json BLOB NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(actor_id,action,target_id,command_key))`},
	{`CREATE TABLE clusters(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner_id TEXT NOT NULL REFERENCES accounts(id),uid TEXT NOT NULL UNIQUE,identity TEXT NOT NULL,kubernetes_version TEXT NOT NULL,state TEXT NOT NULL,credential_revision INTEGER NOT NULL,revision INTEGER NOT NULL,capabilities BLOB NOT NULL,credential BLOB,created_at TEXT NOT NULL)`,
		`CREATE TABLE operations(id TEXT PRIMARY KEY,action TEXT NOT NULL,state TEXT NOT NULL,cluster_id TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES accounts(id),owner_id TEXT NOT NULL,owner_before TEXT NOT NULL DEFAULT '',name TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,credential_revision INTEGER NOT NULL DEFAULT 0)`,
		`CREATE INDEX unfinished_operations ON operations(cluster_id,state)`,
		`CREATE TABLE cluster_candidates(operation_id TEXT PRIMARY KEY REFERENCES operations(id),cipher BLOB NOT NULL)`},
	{`CREATE TABLE discovered_instances(id TEXT PRIMARY KEY,cluster_id TEXT NOT NULL REFERENCES clusters(id),document BLOB NOT NULL)`,
		`CREATE TABLE instances(id TEXT PRIMARY KEY,cluster_id TEXT NOT NULL REFERENCES clusters(id),component TEXT NOT NULL,name TEXT NOT NULL,target_namespace TEXT NOT NULL,uid TEXT NOT NULL DEFAULT '',lifecycle TEXT NOT NULL DEFAULT 'DRAFT',current_version_id TEXT NOT NULL DEFAULT '',activity_at TEXT NOT NULL,UNIQUE(cluster_id,component,name))`,
		`CREATE TABLE drafts(instance_id TEXT PRIMARY KEY REFERENCES instances(id),revision INTEGER NOT NULL,config BLOB NOT NULL)`,
		`CREATE TABLE publication_versions(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES instances(id),source_revision INTEGER NOT NULL,config BLOB NOT NULL,effective_spec BLOB NOT NULL,created_at TEXT NOT NULL)`,
		`CREATE TABLE publication_previews(id TEXT PRIMARY KEY,instance_id TEXT NOT NULL REFERENCES instances(id),draft_revision INTEGER NOT NULL,uid TEXT NOT NULL,target_spec BLOB NOT NULL,desired_spec BLOB NOT NULL,drift INTEGER NOT NULL)`,
		`CREATE TABLE publication_operations(operation_id TEXT PRIMARY KEY REFERENCES operations(id),instance_id TEXT NOT NULL REFERENCES instances(id),version_id TEXT NOT NULL REFERENCES publication_versions(id),preview_id TEXT NOT NULL REFERENCES publication_previews(id),overwrite_drift INTEGER NOT NULL)`,
		`CREATE TABLE execution_attempts(id TEXT PRIMARY KEY,operation_id TEXT NOT NULL REFERENCES operations(id),state TEXT NOT NULL,possibly_sent INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)`,
		`CREATE TABLE instance_protections(instance_id TEXT PRIMARY KEY REFERENCES instances(id),operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id))`,
		`CREATE TABLE instance_observations(instance_id TEXT PRIMARY KEY REFERENCES instances(id),runtime BLOB NOT NULL,cr BLOB NOT NULL,observed_at TEXT NOT NULL)`},
	{`CREATE TABLE instances_new(id TEXT PRIMARY KEY,cluster_id TEXT NOT NULL REFERENCES clusters(id),component TEXT NOT NULL,name TEXT NOT NULL,target_namespace TEXT NOT NULL,uid TEXT NOT NULL DEFAULT '',lifecycle TEXT NOT NULL DEFAULT 'DRAFT',current_version_id TEXT NOT NULL DEFAULT '',activity_at TEXT NOT NULL,cr_namespace TEXT NOT NULL DEFAULT '',UNIQUE(cluster_id,component,cr_namespace,name),CHECK((component='kube-state-metrics' AND cr_namespace='') OR (component='Prometheus Agent' AND cr_namespace<>'' AND cr_namespace=target_namespace)))`,
		`INSERT INTO instances_new(id,cluster_id,component,name,target_namespace,uid,lifecycle,current_version_id,activity_at,cr_namespace) SELECT id,cluster_id,component,name,target_namespace,uid,lifecycle,current_version_id,activity_at,'' FROM instances`,
		`DROP TABLE instances`, `ALTER TABLE instances_new RENAME TO instances`,
		`CREATE TABLE instance_source_observations(instance_id TEXT NOT NULL REFERENCES instances(id),source TEXT NOT NULL,uid TEXT NOT NULL,document BLOB NOT NULL,last_attempt_at TEXT NOT NULL,last_success_at TEXT NOT NULL,error TEXT NOT NULL,epoch TEXT NOT NULL,PRIMARY KEY(instance_id,source))`},
	lifecycleSchema,
	deletionPreviewSchema,
	// v8仅修复已有敏感读cache，没有结构变更；与版本标记在同一事务执行。
	nil,
	activitySchema,
	append(append([]string{}, agentStorageSchema...), storageTasksSchema...),
	resourceProgressSchema,
}

// migrate 配置 SQLite WAL，并在保留恢复依据的前提下原子升级数据库。
func migrate(db *sql.DB, dataDir string, key []byte) error {
	ctx := context.Background()
	conn, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	for _, q := range []string{`PRAGMA journal_mode=WAL`, `PRAGMA synchronous=FULL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`} {
		if _, err := conn.ExecContext(ctx, q); err != nil {
			return fmt.Errorf("数据库配置失败: %w", err)
		}
	}
	var exists int
	if err := conn.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'`).Scan(&exists); err != nil {
		return err
	}
	current := 0
	if exists != 0 {
		var count int
		if err := conn.QueryRowContext(ctx, `SELECT coalesce(max(version),0),count(*) FROM schema_migrations`).Scan(&current, &count); err != nil {
			return err
		}
		// 关键：未知或不连续版本停止启动，避免按错误结构继续写入管理状态。
		if current > schemaVersion || count != current {
			return errors.New("数据库版本不支持，保留原库并停止启动")
		}
	}
	if current < schemaVersion {
		var tables int
		if err := conn.QueryRowContext(ctx, `SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'`).Scan(&tables); err != nil {
			return err
		}
		if tables > 0 {
			dir := filepath.Join(dataDir, "backups")
			if err := os.MkdirAll(dir, 0700); err != nil {
				return fmt.Errorf("迁移前快照目录失败: %w", err)
			}
			path := filepath.Join(dir, fmt.Sprintf("schema-%d-to-%d-%s.db", current, schemaVersion, ID()))
			// 关键：变更结构前先同步包含已提交 WAL 的一致快照，失败时保留可恢复的原库。
			if _, err := conn.ExecContext(ctx, `VACUUM INTO ?`, path); err != nil {
				return fmt.Errorf("迁移前一致快照失败: %w", err)
			}
			if err := os.Chmod(path, 0600); err != nil {
				return err
			}
			snapshot, err := os.Open(path)
			if err != nil {
				return err
			}
			err = snapshot.Sync()
			snapshot.Close()
			if err != nil {
				return err
			}
			folder, err := os.Open(dir)
			if err != nil {
				return err
			}
			err = folder.Sync()
			folder.Close()
			if err != nil {
				return err
			}
		}
		// 关键：父表重建前在固定连接、事务外关闭FK；禁止先rename旧表而改写子表引用。
		if _, err = conn.ExecContext(ctx, `PRAGMA foreign_keys=OFF`); err != nil {
			return err
		}
		defer conn.ExecContext(ctx, `PRAGMA foreign_keys=ON`)
		// 关键：所有DDL、数据投影与版本标记共同提交，失败保留旧关系和预览。
		tx, err := conn.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		defer tx.Rollback()
		for v := current + 1; v <= schemaVersion; v++ {
			for _, q := range migrationSQL[v-1] {
				if _, err = tx.Exec(q); err != nil {
					return fmt.Errorf("数据库迁移失败，原库与快照保留: %w", err)
				}
			}
			if v == 11 {
				if err = migrateResourceProgress(ctx, tx); err != nil {
					return fmt.Errorf("逐资源进度迁移失败: %w", err)
				}
			}
			if v == 9 {
				if err = migrateActivityOrder(tx); err != nil {
					return fmt.Errorf("活动顺序迁移失败: %w", err)
				}
			}
			if v == 5 || v == 8 {
				if err = migrateTargetProjections(tx, key); err != nil {
					return fmt.Errorf("敏感读模型迁移失败，原库与快照保留: %w", err)
				}
			}
			if _, err = tx.Exec(`INSERT INTO schema_migrations(version) VALUES(?)`, v); err != nil {
				return err
			}
		}
		fk, e := tx.Query(`PRAGMA foreign_key_check`)
		if e != nil {
			return e
		}
		broken := fk.Next()
		readErr := fk.Err()
		closeErr := fk.Close()
		// 关键：空结果只有在游标完整成功结束时才证明无外键违规；读取/关闭失败均回滚。
		if readErr != nil {
			return fmt.Errorf("迁移外键核验读取失败: %w", readErr)
		}
		if closeErr != nil {
			return fmt.Errorf("迁移外键核验关闭失败: %w", closeErr)
		}
		if broken {
			return errors.New("迁移产生外键错误，保留原库")
		}
		if err = tx.Commit(); err != nil {
			return fmt.Errorf("数据库迁移提交失败，保留恢复依据: %w", err)
		}
	}
	if _, err = conn.ExecContext(ctx, `PRAGMA foreign_keys=ON`); err != nil {
		return err
	}
	var fkEnabled int
	if err = conn.QueryRowContext(ctx, `PRAGMA foreign_keys`).Scan(&fkEnabled); err != nil || fkEnabled != 1 {
		return errors.New("数据库外键保护未开启")
	}
	for _, q := range []string{`SELECT id,username,password_hash,role,enabled,must_change,auth_version,created_at FROM accounts LIMIT 0`, `SELECT digest,account_id,auth_version,expires_at FROM sessions LIMIT 0`, `SELECT actor_id,action,target_id,command_key,request_digest,result_json FROM command_results LIMIT 0`} {
		rows, err := conn.QueryContext(ctx, q)
		if err != nil {
			return fmt.Errorf("数据库结构不兼容，保留原库: %w", err)
		}
		rows.Close()
	}
	return nil
}
