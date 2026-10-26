import type { Entry } from "../session/controller";

/** Only IDs, row heights and offsets survive outside the mounted window. */
export class TranscriptLayout {
  entries: readonly Entry[] = [];
  indices = new Map<string, number>();
  offsets: number[] = [0];
  revision = 0;
  private heights = new Map<string, number>();
  private context = "";

  sync(entries: readonly Entry[], context: string): void {
    const changed = context !== this.context;
    if (!changed && entries.length === this.entries.length
      && entries.every((entry, index) => entry.id === this.entries[index]!.id)) return;
    if (changed) this.heights.clear();
    this.context = context;
    this.entries = entries;
    this.indices = new Map(entries.map((entry, index) => [entry.id, index]));
    for (const id of this.heights.keys()) if (!this.indices.has(id)) this.heights.delete(id);
    this.rebuild();
  }

  private rebuild(): void {
    this.offsets = [0];
    for (const entry of this.entries) this.offsets.push(this.total + (this.heights.get(entry.id) ?? 6));
    this.revision++;
  }
  get total(): number { return this.offsets[this.offsets.length - 1]!; }
  height(index: number): number { return this.offsets[index + 1]! - this.offsets[index]!; }

  measure(rows: Iterable<readonly [string, number]>): boolean {
    let changed = false;
    for (const [id, height] of rows) {
      if (this.indices.has(id) && this.heights.get(id) !== height) {
        this.heights.set(id, height); changed = true;
      }
    }
    if (changed) this.rebuild();
    return changed;
  }

  indexAt(row: number): number {
    let low = 0, high = this.entries.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (this.offsets[middle + 1]! <= row) low = middle + 1;
      else high = middle;
    }
    return Math.min(low, Math.max(0, this.entries.length - 1));
  }

  window(top: number, height: number, pinned: Iterable<string> = []): number[] {
    if (!this.entries.length) return [];
    const first = this.indexAt(Math.max(0, top - height));
    const last = this.indexAt(top + height * 2);
    const indices = new Set<number>();
    for (let index = first; index <= last; index++) indices.add(index);
    for (const id of pinned) {
      const index = this.indices.get(id);
      if (index !== undefined) indices.add(index);
    }
    return [...indices].sort((a, b) => a - b);
  }
}
