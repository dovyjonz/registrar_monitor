
export function markPerformance(name) {
    if (typeof performance?.mark === 'function') performance.mark(name);
}

export function markAfterAnimationFrames(name, frameCount = 2) {
    if (typeof requestAnimationFrame !== 'function') {
        markPerformance(name);
        return;
    }

    let remaining = frameCount;
    const nextFrame = () => {
        remaining -= 1;
        if (remaining <= 0) {
            markPerformance(name);
            return;
        }
        requestAnimationFrame(nextFrame);
    };
    requestAnimationFrame(nextFrame);
}
