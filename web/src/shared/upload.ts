import { ERROR_CODE_TEXT } from './enums'

export const MAX_UPLOAD_FILES = 10
export const MAX_UPLOAD_SIZE_BYTES = 15 * 1024 * 1024 // 15 MiB = 15,728,640 B (D-02)
export const MAX_IMAGE_PIXELS = 40_000_000
export const ALLOWED_IMAGE_MIME_TYPES: ReadonlyArray<string> = [
  'image/jpeg',
  'image/png',
  'image/webp',
]

export interface UploadValidationResult {
  valid: boolean
  code?:
    | 'NO_FILES'
    | 'TOO_MANY_FILES'
    | 'IMAGE_SIZE_EXCEEDED'
    | 'UNSUPPORTED_IMAGE_FORMAT'
    | 'MEDIA_VIDEO_STREAM_FORBIDDEN'
    | 'IMAGE_PIXEL_LIMIT_EXCEEDED'
  message?: string
  fileIndex?: number
}

const VIDEO_EXTENSIONS = new Set([
  'mp4',
  'm4v',
  'mkv',
  'avi',
  'mov',
  'flv',
  'webm',
  'wmv',
  'ts',
  'm3u8',
  '3gp',
])

const IMAGE_EXTENSIONS = new Set(['jpg', 'jpeg', 'png', 'webp'])

function getFileExtension(name: string): string {
  const parts = name.toLowerCase().split('.')
  return parts.length > 1 ? parts[parts.length - 1] : ''
}

function bytesMatchAscii(bytes: Uint8Array, offset: number, ascii: string): boolean {
  if (bytes.length < offset + ascii.length) return false
  for (let i = 0; i < ascii.length; i += 1) {
    if (bytes[offset + i] !== ascii.charCodeAt(i)) return false
  }
  return true
}

function containsAsciiSequence(bytes: Uint8Array, needle: string, maxOffset?: number): boolean {
  const limit = maxOffset !== undefined ? Math.min(bytes.length, maxOffset) : bytes.length
  const end = limit - needle.length
  for (let i = 0; i <= end; i += 1) {
    if (bytesMatchAscii(bytes, i, needle)) return true
  }
  return false
}

function detectContainerFromHeader(
  bytes: Uint8Array,
): 'jpeg' | 'png' | 'apng' | 'webp' | 'webp_animated' | 'video' | 'unknown' {
  if (bytes.length < 4) return 'unknown'

  // Video containers: MP4/MOV ftyp at offset 4, MKV/WebM 1A 45 DF A3, FLV "FLV\x01", AVI "RIFF....AVI "
  if (bytes.length >= 8 && bytesMatchAscii(bytes, 4, 'ftyp')) return 'video'
  if (bytes[0] === 0x1a && bytes[1] === 0x45 && bytes[2] === 0xdf && bytes[3] === 0xa3) return 'video'
  if (bytes[0] === 0x46 && bytes[1] === 0x4c && bytes[2] === 0x56 && bytes[3] === 0x01) return 'video'
  if (bytes.length >= 12 && bytesMatchAscii(bytes, 0, 'RIFF') && bytesMatchAscii(bytes, 8, 'AVI ')) return 'video'

  // JPEG: FF D8 FF
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'jpeg'

  // PNG: 89 50 4E 47 0D 0A 1A 0A (+ APNG acTL check before IDAT)
  if (
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47 &&
    bytes[4] === 0x0d &&
    bytes[5] === 0x0a &&
    bytes[6] === 0x1a &&
    bytes[7] === 0x0a
  ) {
    const idatPos = findChunkOffset(bytes, 'IDAT')
    const actlPos = findChunkOffset(bytes, 'acTL')
    if (actlPos !== -1 && (idatPos === -1 || actlPos < idatPos)) {
      return 'apng'
    }
    return 'png'
  }

  // WebP: RIFF....WEBP (+ animated ANIM check)
  if (bytes.length >= 12 && bytesMatchAscii(bytes, 0, 'RIFF') && bytesMatchAscii(bytes, 8, 'WEBP')) {
    if (containsAsciiSequence(bytes, 'ANIM', 256)) {
      return 'webp_animated'
    }
    return 'webp'
  }

  return 'unknown'
}

function findChunkOffset(bytes: Uint8Array, tag: string): number {
  for (let i = 8; i <= bytes.length - 4; i += 1) {
    if (bytesMatchAscii(bytes, i, tag)) return i
  }
  return -1
}

async function readImageDimensions(file: File): Promise<{ width: number; height: number }> {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(file)
    const width = bitmap.width
    const height = bitmap.height
    bitmap.close()
    return { width, height }
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      const width = img.naturalWidth
      const height = img.naturalHeight
      URL.revokeObjectURL(url)
      resolve({ width, height })
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('IMAGE_DECODE_FAILED'))
    }
    img.src = url
  })
}

export function isStreamUrlText(value: string): boolean {
  const lower = value.trim().toLowerCase()
  return (
    lower.startsWith('rtsp://') ||
    lower.startsWith('rtmp://') ||
    lower.includes('.m3u8')
  )
}

/**
 * 严格遵循 docs/API.md §5 & §5.1 五步优先级：
 *  1) 张数 1..10 (ALLOW empty 时 0 跳过) -> TOO_MANY_FILES / NO_FILES
 *  2) 大小 <= 15 MiB (15,728,640 B) -> IMAGE_SIZE_EXCEEDED
 *  3) 魔数 / MIME / 载体 -> MEDIA_VIDEO_STREAM_FORBIDDEN / UNSUPPORTED_IMAGE_FORMAT
 *  4) 像素总量 <= 40,000,000 -> IMAGE_PIXEL_LIMIT_EXCEEDED
 */
export async function validateStaticImageFiles(
  files: File[],
  options?: { allowEmpty?: boolean },
): Promise<UploadValidationResult> {
  if (files.length === 0) {
    if (options?.allowEmpty) return { valid: true }
    return {
      valid: false,
      code: 'NO_FILES',
      message: ERROR_CODE_TEXT.NO_FILES,
    }
  }

  if (files.length > MAX_UPLOAD_FILES) {
    return {
      valid: false,
      code: 'TOO_MANY_FILES',
      message: ERROR_CODE_TEXT.TOO_MANY_FILES,
    }
  }

  // Priority ①: file size <= 15 MiB checked BEFORE format/pixels
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]
    if (file.size > MAX_UPLOAD_SIZE_BYTES) {
      return {
        valid: false,
        code: 'IMAGE_SIZE_EXCEEDED',
        message: `第 ${i + 1} 张（${file.name}）超过 15 MiB（15,728,640 B），请压缩后重传`,
        fileIndex: i,
      }
    }
  }

  // Priority ② / ③ / ④: format, video stream container, animated frames
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]
    const ext = getFileExtension(file.name)
    const mime = (file.type || '').toLowerCase()

    if (file.size === 0) {
      return {
        valid: false,
        code: 'UNSUPPORTED_IMAGE_FORMAT',
        message: `第 ${i + 1} 张（${file.name}）为空文件：${ERROR_CODE_TEXT.UNSUPPORTED_IMAGE_FORMAT}`,
        fileIndex: i,
      }
    }

    const buf = await file.slice(0, 4096).arrayBuffer()
    const header = new Uint8Array(buf)
    const detected = detectContainerFromHeader(header)

    if (detected === 'video' || mime.startsWith('video/') || VIDEO_EXTENSIONS.has(ext)) {
      return {
        valid: false,
        code: 'MEDIA_VIDEO_STREAM_FORBIDDEN',
        message: `第 ${i + 1} 张（${file.name}）：${ERROR_CODE_TEXT.MEDIA_VIDEO_STREAM_FORBIDDEN}`,
        fileIndex: i,
      }
    }

    if (detected === 'apng' || detected === 'webp_animated') {
      return {
        valid: false,
        code: 'UNSUPPORTED_IMAGE_FORMAT',
        message: `第 ${i + 1} 张（${file.name}）为多帧动图，${ERROR_CODE_TEXT.UNSUPPORTED_IMAGE_FORMAT}`,
        fileIndex: i,
      }
    }

    if (detected === 'unknown') {
      // If neither magic matches nor extension/mime is acceptable static image
      return {
        valid: false,
        code: 'UNSUPPORTED_IMAGE_FORMAT',
        message: `第 ${i + 1} 张（${file.name}）：${ERROR_CODE_TEXT.UNSUPPORTED_IMAGE_FORMAT}`,
        fileIndex: i,
      }
    }

    if (mime && !ALLOWED_IMAGE_MIME_TYPES.includes(mime)) {
      return {
        valid: false,
        code: 'UNSUPPORTED_IMAGE_FORMAT',
        message: `第 ${i + 1} 张（${file.name}）：${ERROR_CODE_TEXT.UNSUPPORTED_IMAGE_FORMAT}`,
        fileIndex: i,
      }
    }

    if (ext && !IMAGE_EXTENSIONS.has(ext)) {
      return {
        valid: false,
        code: 'UNSUPPORTED_IMAGE_FORMAT',
        message: `第 ${i + 1} 张（${file.name}）：${ERROR_CODE_TEXT.UNSUPPORTED_IMAGE_FORMAT}`,
        fileIndex: i,
      }
    }
  }

  // Priority ⑤: pixel count <= 40,000,000
  for (let i = 0; i < files.length; i += 1) {
    const file = files[i]
    try {
      const { width, height } = await readImageDimensions(file)
      if (width <= 0 || height <= 0) {
        return {
          valid: false,
          code: 'UNSUPPORTED_IMAGE_FORMAT',
          message: `第 ${i + 1} 张（${file.name}）图像解码异常`,
          fileIndex: i,
        }
      }
      if (width * height > MAX_IMAGE_PIXELS) {
        return {
          valid: false,
          code: 'IMAGE_PIXEL_LIMIT_EXCEEDED',
          message: `第 ${i + 1} 张（${file.name}，${width}×${height}）超过 40 MP 像素上限`,
          fileIndex: i,
        }
      }
    } catch {
      return {
        valid: false,
        code: 'UNSUPPORTED_IMAGE_FORMAT',
        message: `第 ${i + 1} 张（${file.name}）图像无法解码：${ERROR_CODE_TEXT.UNSUPPORTED_IMAGE_FORMAT}`,
        fileIndex: i,
      }
    }
  }

  return { valid: true }
}
