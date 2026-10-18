// 显式活动使用数据库单调序号排序，墙钟只负责展示；观察和异步任务完成不能调用此入口。
package control

import (
	"context"
	"database/sql"
	"sort"
	"time"
)

// activitySchema 保留旧活动的时间顺序并建立全库单 writer 的持久计数器。
var activitySchema = []string{
	`ALTER TABLE instances ADD COLUMN activity_sequence INTEGER NOT NULL DEFAULT 0`,
	`CREATE TABLE instance_activity_counter(singleton INTEGER PRIMARY KEY CHECK(singleton=1),sequence INTEGER NOT NULL CHECK(sequence>=0))`,
	`INSERT INTO instance_activity_counter VALUES(1,(SELECT coalesce(max(activity_sequence),0) FROM instances))`,
	`CREATE INDEX instance_activity_order ON instances(cluster_id,activity_sequence DESC,id)`,
}

// recordInstanceActivity 与已经授权的实际编辑/命令同事务提交；不向浏览器暴露i64排序键。
func (s *Store) recordInstanceActivity(ctx context.Context, tx *sql.Tx, id string) error {
	var sequence int64
	// 关键：计数器和实例更新必须一起回滚，避免时钟回拨、同刻或重启改变真实操作顺序。
	if e := tx.QueryRowContext(ctx, `UPDATE instance_activity_counter SET sequence=sequence+1 WHERE singleton=1 AND sequence<9223372036854775807 RETURNING sequence`).Scan(&sequence); e != nil {
		return Error("UNAVAILABLE", "活动顺序暂不可写")
	}
	result, e := tx.ExecContext(ctx, `UPDATE instances SET activity_at=?,activity_sequence=? WHERE id=?`, s.Now().UTC().Format(time.RFC3339Nano), sequence, id)
	if e != nil {
		return Error("UNAVAILABLE", "活动记录失败")
	}
	n, e := result.RowsAffected()
	if e != nil || n != 1 {
		return Error("UNAVAILABLE", "活动实例不可写")
	}
	return nil
}

// migrateActivityOrder 解析原RFC3339Nano，以完整时间而非SQLite浮点日期回填旧活动顺序。
func migrateActivityOrder(tx *sql.Tx) error {
	rows, e := tx.Query(`SELECT id,activity_at FROM instances`)
	if e != nil {
		return e
	}
	type activity struct {
		id string
		at time.Time
	}
	entries := []activity{}
	for rows.Next() {
		var id, stamp string
		if e = rows.Scan(&id, &stamp); e != nil {
			rows.Close()
			return e
		}
		at, err := time.Parse(time.RFC3339Nano, stamp)
		if err != nil {
			rows.Close()
			return Error("UNAVAILABLE", "旧活动时间无法迁移")
		}
		entries = append(entries, activity{id, at})
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	// 关键：亚毫秒精度属于旧库真实顺序；浮点日期会把不同纳秒合并成同刻，错误改变列表优先级。
	sort.Slice(entries, func(a, b int) bool {
		if entries[a].at.Equal(entries[b].at) {
			return entries[a].id < entries[b].id
		}
		return entries[a].at.Before(entries[b].at)
	})
	for index, entry := range entries {
		if _, e = tx.Exec(`UPDATE instances SET activity_sequence=? WHERE id=?`, index+1, entry.id); e != nil {
			return e
		}
	}
	_, e = tx.Exec(`UPDATE instance_activity_counter SET sequence=? WHERE singleton=1`, len(entries))
	return e
}
