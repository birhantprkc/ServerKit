// AI assistant API methods (core primitive — powered by Prompture).
//
// These are bound onto the shared ApiService instance (see services/api/index.js),
// so they run with `this` = the client and reuse `this.baseUrl` / `this.getToken()`.
// All names are prefixed `ai*` to avoid collisions with other domain modules.
//
// `aiStreamChat` does its own fetch + ReadableStream read (the base `request()`
// isn't stream-aware) so the JWT rides in the Authorization header — no token in
// the URL, unlike EventSource.
import { parseSSEChunk } from '../../lib/ai/sse';

export async function aiStatus() {
    return this.request('/ai/status');
}

export async function aiGetSettings() {
    return this.request('/ai/settings');
}

export async function aiGetManagement() { return this.request('/ai/management'); }
export async function aiSaveManagement(body) { return this.request('/ai/management', { method: 'PUT', body }); }
export async function aiPreviewRouting(body) { return this.request('/ai/management/preview', { method: 'POST', body }); }
export async function aiGetUsage(filters = {}) {
    return this.request(`/ai/usage?${new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ''))}`);
}

export async function aiUpdateSettings(body) {
    return this.request('/ai/settings', { method: 'PUT', body });
}

export async function aiTestSettings(body) {
    return this.request('/ai/settings/test', { method: 'POST', body });
}

export async function aiGetProviders() {
    return this.request('/ai/providers');
}

export async function aiGetConnections() {
    return this.request('/ai/connections');
}

export async function aiSaveConnection(body) {
    const path = body.id ? `/ai/connections/${encodeURIComponent(body.id)}` : '/ai/connections';
    return this.request(path, { method: body.id ? 'PUT' : 'POST', body });
}

export async function aiDeleteConnection(id) {
    return this.request(`/ai/connections/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function aiProbeConnection(body) {
    return this.request('/ai/connections/probe', { method: 'POST', body });
}

export async function aiGetModels(provider) {
    return this.request(`/ai/models?provider=${encodeURIComponent(provider)}`);
}

export async function aiListConversations() {
    return this.request('/ai/conversations');
}

export async function aiCreateConversation(body = {}) {
    return this.request('/ai/conversations', { method: 'POST', body });
}

export async function aiGetConversation(id) {
    return this.request(`/ai/conversations/${id}`);
}

export async function aiRenameConversation(id, title) {
    return this.request(`/ai/conversations/${id}`, { method: 'PATCH', body: { title } });
}

export async function aiDeleteConversation(id) {
    return this.request(`/ai/conversations/${id}`, { method: 'DELETE' });
}

export async function aiConfirmAction(body) {
    return this.request('/ai/chat/confirm', { method: 'POST', body });
}

export async function aiCancel(conversationId) {
    return this.request('/ai/chat/cancel', { method: 'POST', body: { conversation_id: conversationId } });
}

// Streaming turn. Reads SSE frames and calls onEvent({ event, data }) for each.
// Resolves when the stream ends; throws on a non-OK pre-flight response.
export async function aiStreamChat(payload, { signal, onEvent } = {}) {
    const token = this.getToken();
    const { activeWorkspaceId } = this.workspace.getSnapshot();
    const res = await fetch(`${this.baseUrl}/ai/chat/stream`, {
        method: 'POST',
        signal,
        headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            ...(token && { Authorization: `Bearer ${token}` }),
            ...(activeWorkspaceId !== 'all' && { 'X-Workspace-Id': activeWorkspaceId }),
        },
        body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) {
        let data = {};
        try { data = await res.json(); } catch { /* non-JSON error body */ }
        const err = new Error(data.error || data.msg || 'AI stream failed');
        err.status = res.status;
        throw err;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, rest } = parseSSEChunk(buffer);
        buffer = rest;
        for (const ev of events) {
            if (onEvent) onEvent(ev);
        }
    }
}
