export class AnimationController {
  private frameIds = new Set<number>();
  private cancelled = false;
  private paused = false;

  start(): void {
    this.reset();
    this.cancelled = false;
    this.paused = false;
  }

  reset(): void {
    this.cancelled = true;
    this.paused = false;
    this.frameIds.forEach((id) => cancelAnimationFrame(id));
    this.frameIds.clear();
  }

  pause(): void {
    this.paused = true;
  }

  resume(): void {
    this.paused = false;
  }

  isCancelled(): boolean {
    return this.cancelled;
  }

  isPaused(): boolean {
    return this.paused;
  }

  requestFrame(callback: FrameRequestCallback): void {
    const id = requestAnimationFrame((time) => {
      this.frameIds.delete(id);
      callback(time);
    });
    this.frameIds.add(id);
  }
}
