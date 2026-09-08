import { useEffect, useState } from 'react';
import api from '../../services/api';
import useSettingFocus from '../../hooks/useSettingFocus';
import { useAuth } from '../../contexts/useAuth.js';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Button } from '@/components/ui/button';
import AIConnectionsSettings from './AIConnectionsSettings';
import { useTranslation } from 'react-i18next';

const AI_CONFIG_CHANGED_EVENT = 'serverkit:ai-config-changed';

const AISettingsTab = () => {
    const { t } = useTranslation();
    const register = useSettingFocus();
    const { isAdmin } = useAuth();
    const [settings, setSettings] = useState({
        enabled: false, provider: '', model: '', endpoint: '',
        pii_redaction: true, injection_detection: true, max_cost_usd: '0.5',
        api_key_set: false,
    });
    const [providers, setProviders] = useState([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState(null);

    useEffect(() => {
        if (!isAdmin) { setLoading(false); return; }
        Promise.all([api.aiGetSettings(), api.aiGetProviders()])
            .then(([s, p]) => {
                setSettings((prev) => ({ ...prev, ...s, max_cost_usd: String(s.max_cost_usd ?? '0.5') }));
                setProviders(p.providers || []);
            })
            .catch((e) => setMessage({ type: 'error', text: e.message || 'Failed to load AI settings' }))
            .finally(() => setLoading(false));
    }, [isAdmin]);

    const refreshConnections = async () => {
        const fresh = await api.aiGetSettings();
        setSettings((prev) => ({ ...prev, ...fresh, max_cost_usd: String(fresh.max_cost_usd ?? '0.5') }));
        window.dispatchEvent(new Event(AI_CONFIG_CHANGED_EVENT));
        return fresh;
    };

    const buildPayload = () => {
        const payload = {
            enabled: settings.enabled,
            pii_redaction: settings.pii_redaction,
            injection_detection: settings.injection_detection,
            max_cost_usd: settings.max_cost_usd,
        };
        return payload;
    };

    const handleSave = async () => {
        setSaving(true);
        setMessage(null);
        try {
            await api.aiUpdateSettings(buildPayload());
            const fresh = await api.aiGetSettings();
            setSettings((prev) => ({ ...prev, ...fresh, max_cost_usd: String(fresh.max_cost_usd ?? '0.5') }));
            window.dispatchEvent(new Event(AI_CONFIG_CHANGED_EVENT));
            setMessage({ type: 'success', text: 'AI settings saved' });
        } catch (e) {
            setMessage({ type: 'error', text: e.message || 'Failed to save AI settings' });
        } finally {
            setSaving(false);
        }
    };

    if (!isAdmin) {
        return <div className="settings-section"><p>{t('app.aISettingsTab.adminAccessRequired', 'Admin access required.')}</p></div>;
    }
    if (loading) {
        return <div className="settings-section"><p>{t('common.loading', 'Loading…')}</p></div>;
    }


    return (
        <div className="settings-section">
            <h2>{t('app.aISettingsTab.aiAssistant', 'AI Assistant')}</h2>
            <p className="section-description">
                {t('app.aISettingsTab.configureTheInPanelAssistantPowered', 'Configure the in-panel assistant — powered by Prompture. The API key is stored encrypted and never returned by the API.')}
            </p>

            {message && <div className={`message ${message.type}`}>{message.text}</div>}

            <div {...register('ai-provider')}>
                <AIConnectionsSettings connections={settings.connections || []} providers={providers}
                    defaultId={settings.default_connection_id} onSaved={refreshConnections} />
            </div>
            <div className="settings-card">
                <div className="form-group">
                    <div className="settings-row">
                        <div className="settings-label"><Label>{t('app.aISettingsTab.enableAiAssistant', 'Enable AI assistant')}</Label></div>
                        <Switch
                            checked={settings.enabled}
                            onCheckedChange={(v) => setSettings((s) => ({ ...s, enabled: v }))}
                        />
                    </div>
                </div>

                <div className="form-group">
                    <label htmlFor="ai-max-cost">{t('app.aISettingsTab.perConversationCostCeilingUsd', 'Per-conversation cost ceiling (USD)')}</label>
                    <input
                        id="ai-max-cost"
                        type="number"
                        step="0.1"
                        min="0"
                        value={settings.max_cost_usd}
                        onChange={(e) => setSettings((s) => ({ ...s, max_cost_usd: e.target.value }))}
                    />
                </div>

                <div className="form-group">
                    <div className="settings-row">
                        <div className="settings-label"><Label>{t('app.aISettingsTab.redactPiiFromMessagesToolOutput', 'Redact PII from messages & tool output')}</Label></div>
                        <Switch
                            checked={settings.pii_redaction}
                            onCheckedChange={(v) => setSettings((s) => ({ ...s, pii_redaction: v }))}
                        />
                    </div>
                </div>
                <div className="form-group">
                    <div className="settings-row">
                        <div className="settings-label"><Label>{t('app.aISettingsTab.blockPromptInjectionAttempts', 'Block prompt-injection attempts')}</Label></div>
                        <Switch
                            checked={settings.injection_detection}
                            onCheckedChange={(v) => setSettings((s) => ({ ...s, injection_detection: v }))}
                        />
                    </div>
                </div>

                <div className="settings-actions">
                    <Button variant="primary" onClick={handleSave} disabled={saving}>
                        {saving ? 'Saving…' : 'Save'}
                    </Button>
                </div>
            </div>
        </div>
    );
};

export default AISettingsTab;
