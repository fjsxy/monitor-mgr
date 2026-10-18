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
	// 有密文的旧库必须先用原密钥验证，再制作迁移快照或推进 schema。
	if err = s.verifyExistingCredentials(); err != nil {
		db.Close()
		return fail(err)
	}
	if err = migrate(db, dataDir); err != nil {
		db.Close()
		return fail(err)
	}

	return s, nil
}
func (s *Store) Close() error {
	s.StopWorkers()
	err := s.DB.Close()
	unix.Flock(int(s.lock.Fd()), unix.LOCK_UN)
	s.lock.Close()
	return err
}
func ID() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
func Error(code, message string) error { return &rpc.APIError{Code: code, Message: message} }

var usernameRE = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,62}$`)

func validCredentials(name, password string) error {
	if !usernameRE.MatchString(name) {
		return Error("INVALID_INPUT", "账号须为 1–63 位英文字母、数字、点、下划线或连字符")
	}
	return validPassword(password)
}
func validPassword(password string) error {
	if len(password) < 12 || len(password) > 72 {
		return Error("INVALID_INPUT", "密码须为 12–72 字节")
	}
	return nil
}
func scanAccount(row interface{ Scan(...any) error }) (*rpc.Account, error) {
	a := new(rpc.Account)
	err := row.Scan(&a.Id, &a.Username, &a.Role, &a.Enabled, &a.MustChangePassword, &a.CreatedAt)
	return a, err
}

const accountColumns = `id,username,role,enabled,must_change,created_at`

func (s *Store) Probe(ctx context.Context, _ *rpc.RequestContext) (*rpc.ProbeResult_, error) {
	var n int
	if err := s.DB.QueryRowContext(ctx, `SELECT count(*) FROM accounts`).Scan(&n); err != nil {
		return nil, Error("UNAVAILABLE", "数据库暂不可用")
	}
	return &rpc.ProbeResult_{Initialized: n > 0}, nil
}
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
