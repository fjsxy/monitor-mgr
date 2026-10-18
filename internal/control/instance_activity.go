// 显式活动使用数据库单调序号排序，墙钟只负责展示；观察和异步任务完成不能调用此入口。
package control

import (
	"context"
	"database/sql"
	"time"
)

// activitySchema 保留旧活动的时间顺序并建立全库单 writer 的持久计数器。
var activitySchema = []string{
	`ALTER TABLE instances ADD COLUMN activity_sequence INTEGER NOT NULL DEFAULT 0`,
	`WITH ranked AS (SELECT id,row_number() OVER (ORDER BY julianday(activity_at),id) AS seq FROM instances) UPDATE instances SET activity_sequence=(SELECT seq FROM ranked WHERE ranked.id=instances.id)`,
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
