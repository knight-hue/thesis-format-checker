// 原始 docx 的浏览器本地持久化（IndexedDB）。
//
// 为什么要它：检测结果页刷新后还能靠 sessionStorage 快照还原，但内存里的 File（rawSession.originalFile）
// 是拿不回来的，而「修复并下载 / 导出修改后的文档」必须要原始文件。这里把原始文件存到浏览器本地，
// 刷新后重新捞出来还原成 File 对象。
//
// 所有函数都不抛异常：IndexedDB 不可用（隐私模式、配额不足）时只返回 false / null，由调用方兜底提示。
import type { PaperFile } from '../types/store'

interface StoredFile {
  id: string
  name: string
  type: string
  lastModified: number
  blob: PaperFile
}

const DB_NAME = 'thesis-format-checker'
const STORE_NAME = 'files'
const FILE_KEY = 'originalFile'
const PENDING_FILE_KEY = 'pendingCheckFile'

function openDb() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    if (!globalThis.indexedDB) {
      reject(new Error('当前浏览器不支持 IndexedDB'))
      return
    }
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error || new Error('打开本地数据库失败'))
    req.onblocked = () => reject(new Error('本地数据库被其他标签页占用'))
  })
}

function runTransaction<T>(db: IDBDatabase, mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, mode)
    const result = action(tx.objectStore(STORE_NAME))
    tx.oncomplete = () => resolve(result.result)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })
}

// 保存原始文件（同一个 key，重新上传会覆盖旧文件）。
async function saveFile(file: PaperFile | null, key: string): Promise<boolean> {
  if (!file) return false
  let db: IDBDatabase | undefined
  try {
    db = await openDb()
    await runTransaction(db, 'readwrite', (store) =>
      store.put({
        id: key,
        name: file.name || 'paper.docx',
        type: file.type || '',
        lastModified: file.lastModified || Date.now(),
        blob: file,
      })
    )
    return true
  } catch (e) {
    return false
  } finally {
    if (db) db.close()
  }
}

export function saveOriginalFile(file: PaperFile | null) {
  return saveFile(file, FILE_KEY)
}

export function savePendingFile(file: PaperFile | null) {
  return saveFile(file, PENDING_FILE_KEY)
}

// 取回原始文件并还原成 File（FormData 上传需要文件名）；没有或失败返回 null。
async function loadFile(key: string): Promise<PaperFile | null> {
  let db: IDBDatabase | undefined
  try {
    db = await openDb()
    const record = await runTransaction<StoredFile | undefined>(db, 'readonly', (store) => store.get(key))
    if (!record || !record.blob) return null
    if (typeof File !== 'function') return record.blob
    return new File([record.blob], record.name || 'paper.docx', {
      type: record.type || '',
      lastModified: record.lastModified || Date.now(),
    })
  } catch (e) {
    return null
  } finally {
    if (db) db.close()
  }
}

export function loadOriginalFile() {
  return loadFile(FILE_KEY)
}

export function loadPendingFile() {
  return loadFile(PENDING_FILE_KEY)
}

// 清掉本地缓存的原始文件。
async function clearFile(key: string): Promise<boolean> {
  let db: IDBDatabase | undefined
  try {
    db = await openDb()
    await runTransaction(db, 'readwrite', (store) => store.delete(key))
    return true
  } catch (e) {
    return false
  } finally {
    if (db) db.close()
  }
}

export function clearOriginalFile() {
  return clearFile(FILE_KEY)
}

export function clearPendingFile() {
  return clearFile(PENDING_FILE_KEY)
}
