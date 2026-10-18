package control

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const schemaVersion = 3

var migrationSQL = [][]string{
	{`CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)`,
		`CREATE TABLE accounts(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash BLOB NOT NULL,role TEXT NOT NULL CHECK(role IN ('ADMIN','USER')),enabled INTEGER NOT NULL,must_change INTEGER NOT NULL,auth_version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)`,
		`CREATE TABLE sessions(digest TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),auth_version INTEGER NOT NULL,expires_at TEXT NOT NULL)`,
		`CREATE TABLE audit(id TEXT PRIMARY KEY,actor_id TEXT,action TEXT NOT NULL,target_id TEXT,created_at TEXT NOT NULL)`},
	{`CREATE TABLE command_results(actor_id TEXT NOT NULL,action TEXT NOT NULL,target_id TEXT NOT NULL,command_key TEXT NOT NULL,request_digest TEXT NOT NULL,result_json BLOB NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(actor_id,action,target_id,command_key))`},
	{`CREATE TABLE clusters(id TEXT PRIMARY KEY,name TEXT NOT NULL,owner_id TEXT NOT NULL REFERENCES accounts(id),uid TEXT NOT NULL UNIQUE,identity TEXT NOT NULL,kubernetes_version TEXT NOT NULL,state TEXT NOT NULL,credential_revision INTEGER NOT NULL,revision INTEGER NOT NULL,capabilities BLOB NOT NULL,credential BLOB,created_at TEXT NOT NULL)`,
		`CREATE TABLE operations(id TEXT PRIMARY KEY,action TEXT NOT NULL,state TEXT NOT NULL,cluster_id TEXT NOT NULL,actor_id TEXT NOT NULL REFERENCES accounts(id),owner_id TEXT NOT NULL,name TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,credential_revision INTEGER NOT NULL DEFAULT 0)`,
		`CREATE INDEX unfinished_operations ON operations(cluster_id,state)`,
		`CREATE TABLE cluster_candidates(operation_id TEXT PRIMARY KEY REFERENCES operations(id),cipher BLOB NOT NULL)`},
}

func migrate(db *sql.DB, dataDir string) error {
	for _, q := range []string{`PRAGMA journal_mode=WAL`, `PRAGMA synchronous=FULL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`} {
		if _, err := db.Exec(q); err != nil {
			return fmt.Errorf("数据库配置失败: %w", err)
		}
	}
	var exists int
	if err := db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'`).Scan(&exists); err != nil {
		return err
	}
	current := 0
	if exists != 0 {
		var count int
		if err := db.QueryRow(`SELECT coalesce(max(version),0),count(*) FROM schema_migrations`).Scan(&current, &count); err != nil {
			return err
		}
		if current > schemaVersion || count != current {
			return errors.New("数据库版本不支持，保留原库并停止启动")
		}
	}
	if current < schemaVersion {
		var tables int
		if err := db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'`).Scan(&tables); err != nil {
			return err
		}
		if tables > 0 {
			dir := filepath.Join(dataDir, "backups")
			if err := os.MkdirAll(dir, 0700); err != nil {
				return fmt.Errorf("迁移前快照目录失败: %w", err)
			}
			path := filepath.Join(dir, fmt.Sprintf("schema-%d-to-%d-%s.db", current, schemaVersion, ID()))
			// VACUUM INTO obtains a consistent SQLite snapshot including committed WAL data.
			if _, err := db.Exec(`VACUUM INTO ?`, path); err != nil {
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
		tx, err := db.Begin()
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
			if _, err = tx.Exec(`INSERT INTO schema_migrations(version) VALUES(?)`, v); err != nil {
				return err
			}
		}
		if err = tx.Commit(); err != nil {
			return fmt.Errorf("数据库迁移提交失败，保留恢复依据: %w", err)
		}
	}
	for _, q := range []string{`SELECT id,username,password_hash,role,enabled,must_change,auth_version,created_at FROM accounts LIMIT 0`, `SELECT digest,account_id,auth_version,expires_at FROM sessions LIMIT 0`, `SELECT actor_id,action,target_id,command_key,request_digest,result_json FROM command_results LIMIT 0`} {
		rows, err := db.Query(q)
		if err != nil {
			return fmt.Errorf("数据库结构不兼容，保留原库: %w", err)
		}
		rows.Close()
	}
	return nil
}
