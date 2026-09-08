import { FormField } from '../FormField';
import { useServerkitAI } from '../../contexts/useServerkitAI';
import { useTranslation } from 'react-i18next';
import ModelBinding from './ModelBinding';

export default function ConnectionSelector() {
    const { t } = useTranslation();
    const { connections, selectedConnection, selectedModel, selectConnection, selectModel,
        profile, workflow, selectProfile, selectWorkflow, activeId, isStreaming } = useServerkitAI();
    if (activeId || !connections.length) return null;
    return (
        <div className="sk-ai-task-selector">
          <div className="sk-ai-connection-selector">
            <FormField htmlFor="ai-chat-workflow" label={t('ai.tasks.task', 'Task')}>
                <select id="ai-chat-workflow" value={workflow} disabled={isStreaming} onChange={(e) => selectWorkflow(e.target.value)}>
                    <option value="chat">{t('ai.tasks.chat', 'Chat')}</option>
                    <option value="summarize">{t('ai.tasks.summarize', 'Summarize')}</option>
                    <option value="extract">{t('ai.tasks.extract', 'Extract structured facts')}</option>
                    <option value="diagnose">{t('ai.tasks.diagnose', 'Read-only diagnosis')}</option>
                </select>
            </FormField>
            <FormField htmlFor="ai-chat-profile" label={t('ai.tasks.role', 'Model role')}>
                <select id="ai-chat-profile" value={profile} disabled={isStreaming} onChange={(e) => selectProfile(e.target.value)}>
                    <option value="">{t('ai.tasks.automatic', 'Task default / automatic routing')}</option>
                    <option value="utility">{t('ai.management.utility', 'Utility')}</option>
                    <option value="standard">{t('ai.management.standard', 'Standard')}</option>
                    <option value="advanced">{t('ai.management.advanced', 'Advanced')}</option>
                </select>
            </FormField>
          </div>
          <details><summary>{t('ai.tasks.override', 'Override connection and model')}</summary>
            <ModelBinding id="ai-chat" value={selectedConnection ? { connection_id: selectedConnection, model: selectedModel } : null}
                connections={connections} disabled={isStreaming} allowInherit inheritLabel={t('ai.tasks.useTaskModel', 'Use selected task model')} onChange={(next) => { selectConnection(next?.connection_id || ''); selectModel(next?.model || ''); }} />
          </details>
        </div>
    );
}
