import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import api from '../../services/api';
import { FormField } from '../FormField';
import { Button } from '../ui/button';
import ModelPicker from './ModelPicker';

export default function ModelBinding({ id, value, onChange, connections, disabled, allowInherit = false, inheritLabel, discover = false }) {
    const { t } = useTranslation();
    const [models, setModels] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const connection = connections.find((item) => item.id === value?.connection_id);
    const catalog = models || connection?.model_catalog || [];
    const refresh = async () => {
        setBusy(true); setError('');
        try {
            const result = await api.aiProbeConnection({ ...connection, test: false });
            setModels(result.model_details || result.models || []);
        } catch (e) { setError(e.message); }
        finally { setBusy(false); }
    };
    return <div className="sk-ai-binding">
        <FormField htmlFor={`${id}-connection`} label={t('ai.connections.connection', 'Connection')}>
            <select id={`${id}-connection`} disabled={disabled || busy} value={value?.connection_id || ''} onChange={(event) => {
                const next = connections.find((item) => item.id === event.target.value);
                setModels(null); setError('');
                onChange(next ? { connection_id: next.id, model: next.model } : null);
            }}>
                <option value="">{allowInherit ? (inheritLabel || t('ai.management.inherit', 'Use Standard / panel default')) : t('ai.management.chooseConnection', 'Choose a connection')}</option>
                {connections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
        </FormField>
        {value && <FormField htmlFor={`${id}-model`} label={t('ai.connections.model', 'Model')}>
            <ModelPicker id={`${id}-model`} value={value.model} models={catalog} disabled={disabled || busy}
                onChange={(model) => onChange({ ...value, model })} />
        </FormField>}
        {discover && connection && <Button variant="outline" type="button" disabled={disabled || busy} onClick={refresh}>
            {busy ? t('ai.connections.discovering', 'Discovering…') : t('ai.connections.discover', 'Discover models')}
        </Button>}
        {error && <p className="error-message" role="alert">{error}</p>}
    </div>;
}
