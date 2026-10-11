"""Read-only async queries with source/generation/revision/pair boundaries."""
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from PySide6 import QtCore

from analysis.comparison import AnalysisRepository
from analysis.incremental import read_summary
from collector.latest_task import LatestTask
from .adapters import source_revision


@dataclass(frozen=True)
class Catalog:
    repository: AnalysisRepository
    sessions: tuple
    uid: str | None
    segments: tuple
    segment_id: int | None
    laps: tuple
    quality: tuple
    revision: tuple


class SnapshotChanged(RuntimeError):
    """Retry a live committed revision, rather than mixing successive snapshots."""


def file_identity(database):
    stat = database.stat()
    # File ID/creation time distinguish replacement at an unchanged path. Not
    # an archive-validation substitute: authoritative Raw identity stays in core.
    return (stat.st_dev, stat.st_ino, getattr(stat, 'st_birthtime_ns', stat.st_ctime_ns))


def read_revision(database):
    database = Path(database)
    identity = file_identity(database)
    try:
        summary = read_summary(database)
    except (ValueError, sqlite3.OperationalError):
        # Pre-incremental databases have no revision metadata. Stat is an invalidation
        # fallback, not an algorithm/source equivalence claim.
        stat = database.stat()
        wal = Path(str(database) + '-wal')
        summary = {'analysis_revision': (stat.st_mtime_ns, stat.st_size,
                                         wal.stat().st_mtime_ns if wal.exists() else None)}
    if identity != file_identity(database):
        raise SnapshotChanged('来源文件已替换，重新获取快照')
    return source_revision(database, {**summary, 'source_identity': identity})


def query_catalog(database, desired_uid, desired_segment):
    before = read_revision(database)
    repository = AnalysisRepository(database)
    sessions = repository.sessions()
    usable = [s for s in sessions if s.lap_count]
    default = (usable or sessions)[-1].session_uid if sessions else None
    uid = desired_uid if any(s.session_uid == desired_uid for s in sessions) else default
    segments = repository.segments(uid) if uid is not None else ()
    segment = desired_segment if any(s['id'] == desired_segment for s in segments) else None
    quality = repository.quality(uid, segment) if uid is not None else ()
    laps = repository.laps(uid) if uid is not None else ()
    if segment is not None:
        ids = {q['lap_number'] for q in quality}
        laps = tuple(lap for lap in laps if lap.lap_number in ids)
    revision = read_revision(database)
    if before != revision:
        raise SnapshotChanged('数据库提交已推进，等待稳定快照')
    return Catalog(repository, sessions, uid, segments, segment, laps, quality,
                   revision)


def query_comparison(repository, uid, reference, comparison, revision):
    if read_revision(repository.database) != revision:
        raise SnapshotChanged('所选比较 revision 已推进')
    result = repository.comparison(uid, reference, comparison)
    if read_revision(repository.database) != revision:
        raise SnapshotChanged('比较查询期间 revision 已推进')
    return result


class AnalysisController(QtCore.QObject):
    catalog_ready = QtCore.Signal(object)
    comparison_ready = QtCore.Signal(object, object)
    cleared = QtCore.Signal(str, bool)
    scope_invalidated = QtCore.Signal()
    failed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.catalog_task, self.comparison_task = LatestTask(), LatestTask()
        self.generation = 0
        self.probe = 0
        self.database = None
        self.uid = self.segment_id = None
        self.catalog = None
        self.pair = (None, None)
        self.catalog_key = self.comparison_key = None
        self.display_identity = None
        self.closed = False

    def set_source(self, database):
        if self.closed:
            return
        self.generation += 1
        self.database = Path(database).resolve() if database else None
        self.uid = self.segment_id = None
        self.catalog = None
        self.pair = (None, None)
        self.comparison_key = self.display_identity = None
        self.scope_invalidated.emit()
        self.cleared.emit('正在加载来源…' if database else '等待分析档案…', True)
        self.refresh()

    def set_scope(self, uid, segment):
        if self.closed or (uid, segment) == (self.uid, self.segment_id):
            return
        self.uid, self.segment_id = uid, segment
        self.catalog = None
        self.comparison_key = self.display_identity = None
        self.scope_invalidated.emit()
        self.cleared.emit('正在切换会话 / 驾驶段…', False)
        self.refresh()

    def refresh(self):
        if self.closed or not self.database:
            return
        self.probe += 1
        key = (self.generation, str(self.database), self.uid, self.segment_id, self.probe)
        self.catalog_key = key
        database, uid, segment = self.database, self.uid, self.segment_id
        self.catalog_task.request(key, lambda: query_catalog(database, uid, segment))

    def set_pair(self, reference, comparison):
        if self.closed:
            return
        self.pair = reference, comparison
        catalog = self.catalog
        if not catalog or None in self.pair:
            self.comparison_key = None
            self.cleared.emit('此范围暂无可比较圈次；查看数据质量了解原因。', False)
            return
        identity = (self.generation, str(self.database), self.uid, self.segment_id, *self.pair,
                    catalog.revision[:2])
        key = (*identity, catalog.revision)
        if key == self.comparison_key:
            return
        if identity != self.display_identity:
            self.cleared.emit('正在比较所选圈次…', False)
        self.display_identity = identity
        self.comparison_key = key
        repository, uid = catalog.repository, self.uid
        revision = catalog.revision
        self.comparison_task.request(key, lambda: query_comparison(repository, uid, reference, comparison, revision))

    def poll(self):
        if self.closed:
            return
        result = self.catalog_task.take()
        if result and result[0] == self.catalog_key:
            key, catalog, error = result
            if isinstance(error, SnapshotChanged):
                self.refresh()
            elif error:
                self.catalog = None
                self.comparison_key = self.display_identity = None
                self.scope_invalidated.emit()
                self.cleared.emit(f'来源不可用：{type(error).__name__}: {error}', True)
                self.failed.emit(str(error))
            else:
                if self.catalog is not None and self.catalog.revision[:2] != catalog.revision[:2]:
                    self.comparison_key = self.display_identity = None
                    self.scope_invalidated.emit()
                    self.cleared.emit('来源文件已替换，正在重新加载…', False)
                unchanged = (self.catalog is not None and self.catalog.revision == catalog.revision
                             and (self.catalog.uid, self.catalog.segment_id) == (catalog.uid, catalog.segment_id))
                self.catalog = catalog
                self.uid, self.segment_id = catalog.uid, catalog.segment_id
                if not unchanged:
                    self.catalog_ready.emit(catalog)
        result = self.comparison_task.take()
        if result and result[0] == self.comparison_key:
            key, model, error = result
            if isinstance(error, SnapshotChanged):
                self.refresh()
            elif error:
                self.cleared.emit(f'比较失败：{type(error).__name__}: {error}', False)
                self.failed.emit(str(error))
            else:
                self.comparison_ready.emit(model, self.display_identity)

    def close(self):
        self.closed = True
        self.generation += 1
        self.catalog_task.close()
        self.comparison_task.close()

    def ready(self):
        return self.catalog_task.ready() and self.comparison_task.ready()
