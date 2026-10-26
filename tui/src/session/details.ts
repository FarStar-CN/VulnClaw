import type { ExecutionDetailRef } from "../transport/protocol.generated";

export interface DetailPage {
  id: string; offset: number; next_offset: number; bytes: number; text: string;
}

export class ExecutionDetailCache {
  private pages = new Map<string, DetailPage>();
  private pending = new Map<string, Promise<DetailPage>>();
  private bytes = 0;
  private generation = 0;

  constructor(private load: (id: string, offset: number) => Promise<DetailPage>,
    private maxBytes = 1024 * 1024) {}

  read(ref: ExecutionDetailRef, offset = 0): Promise<DetailPage> {
    const key = JSON.stringify([ref.id, offset]);
    const cached = this.pages.get(key);
    if (cached) {
      this.pages.delete(key); this.pages.set(key, cached);
      return Promise.resolve(cached);
    }
    const existing = this.pending.get(key);
    if (existing) return existing;
    const generation = this.generation;
    const request = this.load(ref.id, offset).then((page) => {
      if (generation !== this.generation) return page;
      const size = Buffer.byteLength(page.text);
      if (size <= this.maxBytes) {
        while (this.bytes + size > this.maxBytes) {
          const [oldest, value] = this.pages.entries().next().value!;
          this.pages.delete(oldest); this.bytes -= Buffer.byteLength(value.text);
        }
        this.pages.set(key, page); this.bytes += size;
      }
      return page;
    }).finally(() => {
      if (this.pending.get(key) === request) this.pending.delete(key);
    });
    this.pending.set(key, request);
    return request;
  }

  clear(): void {
    this.generation++; this.pages.clear(); this.pending.clear(); this.bytes = 0;
  }
}
