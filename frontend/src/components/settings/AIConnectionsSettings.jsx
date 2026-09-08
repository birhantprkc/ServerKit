import { FormField } from '../FormField';
import { useRef, useState } from 'react';
import api from '../../services/api';
import { useConfirm } from '../../hooks/useConfirm';
import { Button } from '../ui/button';
import { useTranslation } from 'react-i18next';

const empty = () => ({ id: '', name: '', provider: '', model: '', config: {}, secrets_set: [], make_default: false });

export default function AIConnectionsSettings({ connections, providers, defaultId, onSaved }) {
    const { t } = useTranslation();
    const { confirm } = useConfirm();
    const [draft, setDraft] = useState(empty);
    const [models, setModels] = useState([]);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState(null);
    const revision = useRef(0);
    const meta = providers.find((provider) => provider.id === draft.provider);

    const edit = (next) => {
        revision.current += 1;
        setDraft(next);
        setModels([]);
        setMessage(null);
    };
    const selectConnection = (id) => {
        const row = connections.find((connection) => connection.id === id);
        edit(row ? { ...row, config: { ...row.config }, make_default: row.id === defaultId } : empty());
    };
    const selectProvider = (provider) => {
        const selected = providers.find((item) => item.id === provider);
        edit({ ...draft, provider, model: '', secrets_set: [],
            config: Object.fromEntries((selected?.fields || []).filter((field) => !field.secret).map((field) => [field.name, field.default])) });
    };
    const setField = (field, value, clear = false) => {
        const config = { ...draft.config, [field.name]: value };
        if (field.secret && !value && !clear) delete config[field.name];
        let secretsSet = draft.secrets_set;
        if (field.type === 'url') {
            for (const secret of meta.fields.filter((item) => item.secret)) delete config[secret.name];
            secretsSet = [];
        }
        revision.current += 1;
        setDraft({ ...draft, config, secrets_set: secretsSet });
        setModels([]);
        setMessage(null);
    };
    const run = async (operation) => {
        const current = revision.current;
        setBusy(true);
        setMessage(null);
        try {
            if (operation === 'save') {
                const row = await api.aiSaveConnection(draft);
                const fresh = await onSaved();
                if (current === revision.current) setDraft({ ...row, make_default: row.id === fresh.default_connection_id });
                setMessage({ type: 'success', text: t('ai.connections.savedMessage', 'Connection saved. Existing chats keep their connection and model.') });
            } else {
                const result = await api.aiProbeConnection({ ...draft, test: operation === 'test' });
                if (current !== revision.current) return;
                if (operation === 'models') {
                    setModels(result.models || []);
                    setMessage({ type: 'success', text: t('ai.connections.modelsFound', '{{count}} models found. You can also enter a model ID manually.', { count: result.models?.length || 0 }) });
                } else setMessage({ type: 'success', text: result.message || t('ai.connections.verified', 'Connection verified.') });
            }
        } catch (error) {
            if (current === revision.current) setMessage({ type: 'error', text: error.message });
        } finally { setBusy(false); }
    };
    const remove = async () => {
        if (!await confirm({ title: t('ai.connections.deleteTitle', 'Delete AI connection?'), message: t('ai.connections.deleteMessage', 'Remove {{name}}? Connections used by chats or selected as default cannot be deleted.', { name: draft.name }), confirmText: t('ai.connections.delete', 'Delete'), variant: 'danger' })) return;
        setBusy(true);
        try {
            await api.aiDeleteConnection(draft.id);
            await onSaved();
            edit(empty());
        } catch (error) { setMessage({ type: 'error', text: error.message }); }
        finally { setBusy(false); }
    };

    return (
        <div className="settings-card">
            <h3>{t('ai.connections.title', 'Provider connections')}</h3>
            <p className="section-description">{t('ai.connections.description', 'Configure native Prompture providers or connect a gateway such as OmniRoute. Credentials stay encrypted on the server.')}</p>
            <FormField htmlFor="ai-connection" label={<>{t('ai.connections.saved', 'Saved connection')}</>}>
                <select id="ai-connection" value={draft.id} onChange={(event) => selectConnection(event.target.value)} disabled={busy}>
                    <option value="">{t('ai.connections.add', 'Add a connection…')}</option>
                    {connections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}{connection.id === defaultId ? t('ai.connections.defaultSuffix', ' (default)') : ''}</option>)}
                </select>
            </FormField>
            <FormField htmlFor="ai-connection-name" label={<>{t('ai.connections.name', 'Connection name')}</>}>
                <input id="ai-connection-name" value={draft.name} maxLength={100} disabled={busy} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder={t('ai.connections.namePlaceholder', 'e.g. Team Anthropic or Local OmniRoute')} />
            </FormField>
            <FormField htmlFor="ai-provider" label={<>{t('ai.connections.provider', 'Provider')}</>}>
                <select id="ai-provider" value={draft.provider} disabled={busy} onChange={(event) => selectProvider(event.target.value)}>
                    <option value="">{t('ai.connections.selectProvider', 'Select a provider…')}</option>
                    {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}{provider.available ? '' : t('ai.connections.missingDependency', ' (dependency required)')}</option>)}
                </select>
                {meta && !meta.available && <p className="error-message">{meta.unavailable_reason}</p>}
            </FormField>
            {!!meta?.presets?.length && <FormField htmlFor="ai-preset" label={<>{t('ai.connections.preset', 'Endpoint preset')}</>}>
                <select id="ai-preset" value="" disabled={busy} onChange={(event) => {
                    const preset = meta.presets.find((item) => item.id === event.target.value);
                    if (preset) setField({ name: 'endpoint', type: 'url' }, preset.endpoint);
                }}>
                    <option value="">{t('ai.connections.choosePreset', 'Custom URL, or choose a preset…')}</option>
                    {meta.presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.id}</option>)}
                </select>
            </FormField>}
            {(meta?.fields || []).map((field) => <FormField key={field.name} htmlFor={`ai-config-${field.name}`} label={<>{field.label}{field.required ? t('ai.connections.required', ' (required)') : t('ai.connections.optional', ' (optional)')}</>}>
                <input id={`ai-config-${field.name}`} type={field.secret ? 'password' : field.type}
                    autoComplete="off" disabled={busy} value={draft.config[field.name] ?? ''}
                    placeholder={field.secret && draft.secrets_set.includes(field.name) ? t('ai.connections.secretConfigured', 'Configured — leave blank to keep') : field.default || ''}
                    onChange={(event) => setField(field, event.target.value)} />
                {field.secret && draft.secrets_set.includes(field.name) && <Button variant="unstyled" type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setField(field, '', true)}>{t('ai.connections.clearSecret', 'Clear saved credential')}</Button>}
                {field.type === 'url' && <p className="form-hint">{t('ai.connections.endpointHelp', 'Use an address reachable from the ServerKit backend. In Docker, localhost refers to the panel container. Changing this URL requires re-entering credentials.')}</p>}
            </FormField>)}
            <FormField htmlFor="ai-model" label={<>{t('ai.connections.defaultModel', 'Default model ID')}</>}>
                <input id="ai-model" list="ai-model-options" value={draft.model} maxLength={256} disabled={busy} onChange={(event) => { revision.current += 1; setDraft({ ...draft, model: event.target.value }); }} placeholder={t('ai.connections.modelPlaceholder', 'Model ID or gateway routing alias')} />
                <datalist id="ai-model-options">{models.map((model) => <option key={model} value={model} />)}</datalist>
            </FormField>
            <FormField>
                <label><input type="checkbox" checked={draft.make_default} disabled={busy} onChange={(event) => setDraft({ ...draft, make_default: event.target.checked })} /> {t('ai.connections.makeDefault', 'Use as the default for new chats')}</label>
            </FormField>
            {message && <div className={`message ${message.type}`} role="status">{message.text}</div>}
            <div className="settings-actions">
                <Button variant="unstyled" type="button" className="btn btn-ghost" disabled={busy || !meta?.available} onClick={() => run('models')}>{t('ai.connections.discover', 'Discover models')}</Button>
                <Button variant="unstyled" type="button" className="btn btn-ghost" disabled={busy || !meta?.available || !draft.model} onClick={() => run('test')}>{t('ai.connections.test', 'Test connection')}</Button>
                <Button variant="unstyled" type="button" className="btn btn-primary" disabled={busy || !meta?.available || !draft.name || !draft.model} onClick={() => run('save')}>{busy ? t('ai.connections.working', 'Working…') : t('ai.connections.save', 'Save connection')}</Button>
                {draft.id && <Button variant="unstyled" type="button" className="btn btn-danger" disabled={busy} onClick={remove}>{t('ai.connections.delete', 'Delete')}</Button>}
            </div>
        </div>
    );
}
