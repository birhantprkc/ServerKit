import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../../src/contexts/AuthContext';
import { LocaleProvider } from '../../src/contexts/LocaleContext';
import { ToastProvider } from '../../src/contexts/ToastContext';
import { ConfirmProvider } from '../../src/contexts/ConfirmContext';
import { AIProvider } from '../../src/contexts/AIContext';
import { WorkspaceProvider } from '../../src/contexts/WorkspaceContext';
import { useServerkitAI } from '../../src/contexts/useServerkitAI';
import AISettingsTab from '../../src/components/settings/AISettingsTab';
import ProfileTab from '../../src/components/settings/ProfileTab';
import NotificationsTab from '../../src/components/settings/NotificationsTab';
import ApiSettingsTab from '../../src/components/settings/ApiSettingsTab';
import { FormField } from '../../src/components/FormField';
import ConnectionSelector from '../../src/components/ai/ConnectionSelector';
import Composer from '../../src/components/ai/Composer';
import Message from '../../src/components/ai/Message';
import '../../src/styles/main.scss';

export function Fixture() {
    const ai = useServerkitAI();
    const pane = new URLSearchParams(window.location.search).get('pane') || 'ai';
    return <>
        <div hidden data-testid="ai-state" data-enabled={ai.enabled} data-ready={ai.providerConfigured} />
        <main className="settings-content">
            {pane === 'ai' && <AISettingsTab />}
            {pane === 'profile' && <ProfileTab />}
            {pane === 'notifications' && <NotificationsTab />}
            {pane === 'api' && <ApiSettingsTab />}
            {pane === 'chat' && <div className="settings-card"><ConnectionSelector /><Composer />{ai.messages.map((message) => <Message key={message.id} message={message} />)}</div>}
            {pane === 'controls' && <div className="settings-card">
                <FormField label="Native field" htmlFor="native-field"><input id="native-field" /></FormField>
                <FormField label="Native select" htmlFor="native-select"><select id="native-select"><option>Option</option></select></FormField>
                <FormField><input type="checkbox" aria-label="Native checkbox" /></FormField>
            </div>}
        </main>
        <div className="fixture-chrome"><div className="sidebar" /><div className="sk-topbar" /><div className="global-statusbar" /></div>
    </>;
}
createRoot(document.getElementById('root')).render(
    <MemoryRouter><LocaleProvider><AuthProvider><WorkspaceProvider><ToastProvider><ConfirmProvider><AIProvider>
        <Fixture />
    </AIProvider></ConfirmProvider></ToastProvider></WorkspaceProvider></AuthProvider></LocaleProvider></MemoryRouter>
);
