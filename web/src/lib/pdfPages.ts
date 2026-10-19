// A scoresheet scanned as a PDF (docs/PLAN.md §10.3a): its pages are drawn on this device into
// pictures, and each goes through the same redaction editor as a photo; only the blacked-out
// JPEG the editor makes is uploaded. The PDF itself never leaves the device, and the server keeps
// refusing PDFs. PDF.js (pdfjs-dist, pinned in package.json) is loaded only when a PDF is chosen.

import { MAX_SIDE } from './redact'

export const PDF_TYPE = 'application/pdf'
/** At most this many pages of a PDF (a sheet is one page; front and back, or one per set). */
export const MAX_PAGES = 8

export function isPdf(file: File): boolean {
  return file.type === PDF_TYPE || /\.pdf$/i.test(file.name)
}

/** The scale that draws a page (in PDF points) with its longer side at `max` px: enough to read
 * every box, the same as a photo's upload. */
export function renderScale(width: number, height: number, max = MAX_SIDE): number {
  const longer = Math.max(width, height)
  return longer > 0 ? Math.min(8, max / longer) : 1
}

/** A name for a page's picture: "sheet.pdf" page 2 → "sheet-p2.jpg". */
export function pageName(name: string, page: number): string {
  return `${name.replace(/\.pdf$/i, '') || 'scan'}-p${page}.jpg`
}

export class PdfUnreadable extends Error {}

/** Every page of the PDF (up to MAX_PAGES) as a JPEG picture, in order. */
export async function pdfPages(file: File): Promise<File[]> {
  const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')])
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default
  const task = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
  let doc: Awaited<typeof task.promise>
  try {
    doc = await task.promise
  } catch (error) {
    void task.destroy()
    throw new PdfUnreadable(error instanceof Error ? error.message : 'unreadable PDF')
  }
  const out: File[] = []
  try {
    for (let k = 1; k <= Math.min(doc.numPages, MAX_PAGES); k++) {
      const page = await doc.getPage(k)
      const base = page.getViewport({ scale: 1 })
      const viewport = page.getViewport({ scale: renderScale(base.width, base.height) })
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(viewport.width)
      canvas.height = Math.round(viewport.height)
      await page.render({ canvas, viewport, background: 'white' }).promise
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.95))
      canvas.width = canvas.height = 0
      if (!blob) throw new PdfUnreadable('the page could not be drawn')
      out.push(new File([blob], pageName(file.name, k), { type: 'image/jpeg' }))
    }
  } finally {
    void task.destroy()
  }
  return out
}
