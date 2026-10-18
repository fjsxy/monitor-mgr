// Package control 统一承担业务授权、SQLite持久化与目标集群访问；HTTP层不能绕过此边界。
package control

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"syscall"
	"time"

	rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"
	"golang.org/x/crypto/bcrypt"
	"golang.org/x/sys/unix"
	_ "modernc.org/sqlite"
)

// Store 持有单 writer 数据库、独立密钥和应用生命周期 worker。
type Store struct {
	OperationBoundary func(stage, operationID string) error
	DB                *sql.DB
	lock              *os.File
	Now               func() time.Time
	SessionTTL        time.Duration
	Key               []byte
	limits            *targetLimits
	workerCancel      context.CancelFunc
	workerWake        chan struct{}
	workerDone        chan struct{}
	dispatch          sync.RWMutex
	publicationWG     sync.WaitGroup
	observationEpoch  string
}

// Open 在单 writer 锁、密钥校验与数据库迁移均通过后提供存储服务。
func Open(dataDir string, now func() time.Time, ttl time.Duration) (*Store, error) {
	if now == nil {
		now = time.Now
	}
	if ttl == 0 {
		ttl = 8 * time.Hour
	}
	if ttl < time.Second {
		return nil, errors.New("Session 寿命必须至少一秒")
	}
	if err := os.MkdirAll(dataDir, 0700); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(dataDir, "writer.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	// 关键：同一目录只允许一个 writer，避免并行实例破坏任务和数据库的一致性。
	if err = unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		lock.Close()
		return nil, errors.New("数据目录已有 writer")
	}
	fail := func(err error) (*Store, error) {
		unix.Flock(int(lock.Fd()), unix.LOCK_UN)
		lock.Close()
		return nil, err
	}
	keyPath := filepath.Join(dataDir, "master.key")
	dbPath := filepath.Join(dataDir, "platform.db")
	key, err := os.ReadFile(keyPath)
	// 关键：已有数据库缺原密钥必须停止；生成替代密钥会使历史凭据永久不可解密。
	if os.IsNotExist(err) {
		if _, e := os.Stat(dbPath); e == nil {
			return fail(errors.New("已有数据库缺少原密钥"))
		}
		key = make([]byte, 32)
		if _, err = rand.Read(key); err != nil {
			return fail(err)
		}
		file, e := os.OpenFile(keyPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if e != nil {
			return fail(e)
		}
		_, err = file.Write(key)
		if err == nil {
			err = file.Sync()
		}
		file.Close()
		if err != nil {
			return fail(err)
		}
	} else if err != nil {
		return fail(err)
	}
	// 关键：密钥只允许当前运行 UID 的 0600 普通文件，防止泄露或意外替换。
	info, err := os.Lstat(keyPath)
	if err != nil {
		return fail(err)
	}
	if len(key) != 32 || info.Mode().Perm() != 0600 || !info.Mode().IsRegular() {
		return fail(errors.New("原密钥必须是 32 字节 0600 普通文件"))
	}
	if st, ok := info.Sys().(*syscall.Stat_t); !ok || st.Uid != uint32(os.Getuid()) {
		return fail(errors.New("原密钥 owner 与运行 UID 不符"))
	}
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return fail(err)
	}
	db.SetMaxOpenConns(1)

	s := &Store{DB: db, lock: lock, Now: now, SessionTTL: ttl, Key: key, limits: newTargetLimits(), observationEpoch: ID()}
	// 关键：有密文旧库必须先验证原密钥，再制作快照或推进schema，错误密钥不能改变恢复基线。
	if err = s.verifyExistingCredentials(); err != nil {
		db.Close()
		return fail(err)
	}
	if err = migrate(db, dataDir, key); err != nil {
		db.Close()
		return fail(err)
	}

	return s, nil
}

// Close 先停止 worker，再关闭数据库并释放 writer 锁。
func (s *Store) Close() error {
	s.StopWorkers()
	err := s.DB.Close()
	unix.Flock(int(s.lock.Fd()), unix.LOCK_UN)
	s.lock.Close()
	return err
}

// ID 生成不可预测的独立标识，供实体、操作和会话等入口使用。
func ID() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}

// Error 将安全的业务错误转换为统一 RPC 错误，不包含凭据材料。
func Error(code, message string) error { return &rpc.APIError{Code: code, Message: message} }

var usernameRE = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$`)

// validCredentials 组合账号名称和密码的输入约束。
func validCredentials(name, password string) error {
	if !usernameRE.MatchString(name) {
		return Error("INVALID_INPUT", "账号须为 1–63 位英文字母、数字、点、下划线或连字符")
	}
	return validPassword(password)
}

// validPassword 限制密码字节长度，与 bcrypt 支持范围保持一致。
func validPassword(password string) error {
	if len(password) < 12 || len(password) > 72 {
		return Error("INVALID_INPUT", "密码须为 12–72 字节")
	}
	return nil
}

// scanAccount 将账号的非敏感列转换为 RPC 返回对象。
func scanAccount(row interface{ Scan(...any) error }) (*rpc.Account, error) {
	a := new(rpc.Account)
	err := row.Scan(&a.Id, &a.Username, &a.Role, &a.Enabled, &a.MustChangePassword, &a.CreatedAt)
	return a, err
}

const accountColumns = `id,username,role,enabled,must_change,created_at`

// Probe 通过实际数据库查询报告初始化状态，不依赖目标集群是否在线。
func (s *Store) Probe(ctx context.Context, _ *rpc.RequestContext) (*rpc.ProbeResult_, error) {
	var n int
	if err := s.DB.QueryRowContext(ctx, `SELECT count(*) FROM accounts`).Scan(&n); err != nil {
		return nil, Error("UNAVAILABLE", "数据库暂不可用")
	}
	return &rpc.ProbeResult_{Initialized: n > 0}, nil
}

// Initialize 仅在空账号库中原子建立首个管理员及审计记录。
func (s *Store) Initialize(ctx context.Context, _ *rpc.RequestContext, r *rpc.InitializeRequest) (*rpc.Account, error) {
	if err := validCredentials(r.Username, r.Password); err != nil {
		return nil, err
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(r.Password), bcrypt.DefaultCost)
	if err != nil {
		return nil, Error("INVALID_INPUT", "密码无效")
	}
	tx, err := s.DB.BeginTx(ctx, nil)
	if err != nil {
		return nil, Error("UNAVAILABLE", "数据库暂不可用")
	}
	defer tx.Rollback()
	var count int
	if err = tx.QueryRowContext(ctx, `SELECT count(*) FROM accounts`).Scan(&count); err != nil {
		return nil, Error("UNAVAILABLE", "数据库暂不可用")
	}
	// 关键：事务内再次确认空库，避免并发初始化覆盖既有管理员。
	if count != 0 {
		return nil, Error("ALREADY_INITIALIZED", "管理员已初始化")
	}
	a := &rpc.Account{Id: ID(), Username: r.Username, Role: "ADMIN", Enabled: true, CreatedAt: s.Now().UTC().Format(time.RFC3339Nano)}
	if _, err = tx.ExecContext(ctx, `INSERT INTO accounts(id,username,password_hash,role,enabled,must_change,created_at) VALUES(?,?,?,'ADMIN',1,0,?)`, a.Id, a.Username, hash, a.CreatedAt); err != nil {
		return nil, Error("UNAVAILABLE", "初始化失败")
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO audit VALUES(?,?,?,?,?)`, ID(), a.Id, "INITIALIZE", a.Id, a.CreatedAt); err != nil {
		return nil, Error("UNAVAILABLE", "初始化失败")
	}
	if err = tx.Commit(); err != nil {
		return nil, Error("UNAVAILABLE", "初始化失败")
	}
	return a, nil
}
