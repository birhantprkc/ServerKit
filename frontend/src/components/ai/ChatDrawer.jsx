import { useCallback, useRef, useState } from 'react';
import { useServerkitAI } from '../../contexts/useServerkitAI.js';
import useMediaQuery from '../../hooks/useMediaQuery';
import { useLockBodyScroll } from '../../hooks/useLockBodyScroll';
import useFocusTrap from '../../hooks/ai/useFocusTrap';
import DrawerHeader from './DrawerHeader';
import ContextChip from './ContextChip';
import MessageList from './MessageList';
import Composer from './Composer';
import ConnectionSelector from './ConnectionSelector';
import ShellDockTabs from '../ShellDockTabs';
import { useTranslation } from 'react-i18next';

const WIDTH_KEY = 'sk-ai:width';
const MIN_W = 360;

const clampWidth = (w) => {
    const max = Math.min(560, Math.round(window.innerWidth * 0.9));
    return Math.max(MIN_W, Math.min(w, max));
};

const ChatDrawer = () => {
    const { t } = useTranslation();
    const { close } = useServerkitAI();
    const isMobile = useMediaQuery('(max-width: 768px)');
    const panelRef = useRef(null);
    const [width, setWidth] = useState(() => {
        const saved = parseInt(localStorage.getItem(WIDTH_KEY) || '', 10);
        return Number.isFinite(saved) ? saved : 420;
    });

    useFocusTrap(panelRef, { active: true, onEscape: close });

    // Lock body scroll on mobile (full-screen sheet).
    useLockBodyScroll(isMobile);

    const startResize = useCallback((e) => {
        if (isMobile) return;
        e.preventDefault();
        const onMove = (ev) => {
            const next = clampWidth(window.innerWidth - ev.clientX);
            setWidth(next);
        };
        const onUp = () => {
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
            setWidth((w) => { localStorage.setItem(WIDTH_KEY, String(w)); return w; });
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
    }, [isMobile]);

    const style = isMobile ? undefined : { '--sk-ai-drawer-w': `${width}px` };

    return (
        <div className="sk-ai-overlay" data-mobile={isMobile ? 'true' : 'false'}>
            {isMobile ? <div className="sk-ai-overlay__backdrop" onClick={close} aria-hidden="true" /> : null}
            <aside
                ref={panelRef}
                id="sk-ai-drawer"
                className="sk-ai-drawer"
                role="dialog"
                aria-modal={isMobile ? 'true' : 'false'}
                aria-label={t('app.chatDrawer.serverkitAiAssistant', 'ServerKit AI assistant')}
                style={style}
            >
                {!isMobile ? (
                    <div className="sk-ai-drawer__resize" onPointerDown={startResize} aria-hidden="true" />
                ) : null}
                <div className="sk-ai-drawer__tabs">
                    <ShellDockTabs expandable={false} />
                </div>
                <DrawerHeader />
                <ConnectionSelector />
                <ContextChip />
                <MessageList />
                <Composer />
            </aside>
        </div>
    );
};

export default ChatDrawer;
