import { FormField } from '../FormField';
import { useServerkitAI } from '../../contexts/useServerkitAI';
import { useTranslation } from 'react-i18next';

export default function ConnectionSelector() {
    const { t } = useTranslation();
    const { connections, selectedConnection, selectedModel, selectConnection, selectModel, activeId, isStreaming } = useServerkitAI();
    if (activeId || !connections.length) return null;
    return (
        <div className="sk-ai-connection-selector">
            <FormField htmlFor="ai-chat-connection" label={<>{t('ai.connections.connection', 'Connection')}</>}>
                <select id="ai-chat-connection" value={selectedConnection} disabled={isStreaming} onChange={(event) => selectConnection(event.target.value)}>
                    <option value="">{t('ai.connections.panelDefault', 'Panel default')}</option>
                    {connections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}
                </select>
            </FormField>
            <FormField htmlFor="ai-chat-model" label={<>{t('ai.connections.model', 'Model')}</>}>
                <input id="ai-chat-model" value={selectedModel} disabled={isStreaming} maxLength={256} onChange={(event) => selectModel(event.target.value)} placeholder={t('ai.connections.connectionDefault', 'Connection default')} />
            </FormField>
        </div>
    );
}
