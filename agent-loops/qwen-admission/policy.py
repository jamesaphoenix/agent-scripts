"""Render-first admission. Inference slowdown never decreases render capacity."""
from datetime import datetime, timezone


def inference_limit(resources, now=None, slowdown=False):
    now = now or datetime.now(timezone.utc)
    try:
        sampled = datetime.fromisoformat(resources['pressureSampledAt'].replace('Z', '+00:00'))
        if (now - sampled).total_seconds() > 15 or resources['admissionPaused']:
            return 0, 'resource_safety'
        if resources['criticalMemoryPressure'] or resources['memoryPressure']:
            return 0, 'memory_pressure'
        rendering = resources['activeRenders'] > 0
        if rendering and resources['cpuPressure']:
            return 0, 'render_priority'
        return (1, 'render_priority' if rendering else 'qwen_slowdown') if rendering or slowdown else (2, 'normal')
    except (KeyError, ValueError, TypeError):
        return 0, 'resource_sample_unavailable'
