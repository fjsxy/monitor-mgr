// 环境探针以实际 UID 在目标卷验证 SQLite WAL/FULL、锁与重开；只输出安全能力摘要。
package main

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	_ "modernc.org/sqlite"
)

// VolumeProof 是准备门禁的可判定结果，不含数据库内容、密钥或卷路径。
type VolumeProof struct {
	UID         int    `json:"uid"`
	JournalMode string `json:"journalMode"`
	Synchronous int    `json:"synchronous"`
	LockBlocked bool   `json:"lockBlocked"`
	ReopenValue int    `json:"reopenValue"`
	KeyMode     string `json:"keyMode"`
	WALObserved bool   `json:"walObserved"`
}

// ProbeVolume 验证当前进程真正使用的卷；受控常量行仅用于检测重开持久性。
func ProbeVolume(directory string, expectedUID int) (VolumeProof, error) {
	proof := VolumeProof{UID: os.Getuid()}
	if proof.UID != expectedUID {
		return proof, errors.New("VOLUME_UID_MISMATCH")
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return proof, errors.New("VOLUME_DIRECTORY_UNWRITABLE")
	}
	key := make([]byte, 32)
	if _, err := rand.Read(key); err != nil {
		return proof, err
	}
	keyPath := filepath.Join(directory, "probe.key")
	keyFile, err := os.OpenFile(keyPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return proof, errors.New("VOLUME_KEY_CREATE_FAILED")
	}
	if _, err = keyFile.Write(key); err != nil {
		keyFile.Close()
		return proof, err
	}
	if err = keyFile.Sync(); err != nil {
		keyFile.Close()
		return proof, err
	}
	if err = keyFile.Close(); err != nil {
		return proof, err
	}
	info, err := os.Stat(keyPath)
	if err != nil {
		return proof, err
	}
	proof.KeyMode = fmt.Sprintf("%04o", info.Mode().Perm())
	databasePath := filepath.Join(directory, "probe.sqlite")
	file, err := os.OpenFile(databasePath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return proof, err
	}
	if err = file.Close(); err != nil {
		return proof, err
	}
	database, err := sql.Open("sqlite", databasePath)
	if err != nil {
		return proof, err
	}
	defer database.Close()
	database.SetMaxOpenConns(1)
	if err = database.QueryRow("PRAGMA journal_mode=WAL").Scan(&proof.JournalMode); err != nil {
		return proof, err
	}
	if _, err = database.Exec("PRAGMA synchronous=FULL"); err != nil {
		return proof, err
	}
	if err = database.QueryRow("PRAGMA synchronous").Scan(&proof.Synchronous); err != nil {
		return proof, err
	}
	if _, err = database.Exec("CREATE TABLE probe(value INTEGER NOT NULL); INSERT INTO probe VALUES(21)"); err != nil {
		return proof, err
	}
	if _, err = os.Stat(databasePath + "-wal"); err == nil {
		proof.WALObserved = true
	} else {
		return proof, err
	}
	writer, err := database.Conn(context.Background())
	if err != nil {
		return proof, err
	}
	defer writer.Close()
	if _, err = writer.ExecContext(context.Background(), "BEGIN IMMEDIATE"); err != nil {
		return proof, err
	}
	defer writer.ExecContext(context.Background(), "ROLLBACK")
	competing, err := sql.Open("sqlite", databasePath)
	if err != nil {
		return proof, err
	}
	defer competing.Close()
	competing.SetMaxOpenConns(1)
	if _, err = competing.Exec("PRAGMA busy_timeout=100"); err != nil {
		return proof, err
	}
	_, lockError := competing.Exec("UPDATE probe SET value=22")
	var sqliteError interface{ Code() int }
	proof.LockBlocked = errors.As(lockError, &sqliteError) && (sqliteError.Code()&0xff == 5 || sqliteError.Code()&0xff == 6)
	if !proof.LockBlocked {
		return proof, errors.New("VOLUME_LOCK_NOT_ENFORCED")
	}
	if _, err = writer.ExecContext(context.Background(), "ROLLBACK"); err != nil {
		return proof, err
	}
	if err = writer.Close(); err != nil {
		return proof, err
	}
	if err = competing.Close(); err != nil {
		return proof, err
	}
	if err = database.Close(); err != nil {
		return proof, err
	}
	reopened, err := sql.Open("sqlite", databasePath)
	if err != nil {
		return proof, err
	}
	defer reopened.Close()
	if err = reopened.QueryRow("SELECT value FROM probe").Scan(&proof.ReopenValue); err != nil {
		return proof, err
	}
	if proof.KeyMode != "0600" || !proof.WALObserved || proof.JournalMode != "wal" || proof.Synchronous != 2 || proof.ReopenValue != 21 {
		return proof, errors.New("VOLUME_CAPABILITY_FAILED")
	}
	return proof, nil
}

func main() {
	directory := flag.String("data-dir", "/data", "待验证卷")
	uid := flag.Int("expected-uid", 10001, "实际运行 UID")
	flag.Parse()
	proof, err := ProbeVolume(*directory, *uid)
	if err != nil {
		// 关键：错误详情可能含本地路径；console 仅保留固定诊断码。
		fmt.Fprintln(os.Stderr, "VOLUME_PROBE_FAILED")
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(proof); err != nil {
		os.Exit(1)
	}
}
