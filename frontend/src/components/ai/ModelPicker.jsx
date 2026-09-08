import { useState } from 'react';
import { Check, ChevronsUpDown } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Popover, PopoverTrigger, PopoverContent } from '../ui/popover';
import { Command, CommandInput, CommandList, CommandEmpty, CommandItem } from '../ui/command';

// Model IDs are opaque: preserve gateway aliases (including slashes) verbatim.
export default function ModelPicker({ id, value, models, onChange, disabled }) {
    const { t } = useTranslation();
    const [open, setOpen] = useState(false);
    return (
        <div className="sk-model-picker">
            <Input id={id} value={value} maxLength={256} disabled={disabled}
                onChange={(event) => onChange(event.target.value)}
                placeholder={t('ai.connections.modelPlaceholder', 'Model ID or gateway routing alias')} />
            <Popover open={open} onOpenChange={setOpen}>
                <PopoverTrigger asChild>
                    <Button type="button" variant="outline" disabled={disabled || !models.length}
                        aria-expanded={open} aria-haspopup="dialog">
                        <ChevronsUpDown size={16} />
                        {t('ai.connections.browseModels', 'Browse models ({{count}})', { count: models.length })}
                    </Button>
                </PopoverTrigger>
                <PopoverContent align="end" className="sk-model-picker__menu"
                    aria-label={t('ai.connections.chooseModel', 'Choose a model')}>
                    <Command>
                        <CommandInput placeholder={t('ai.connections.searchModels', 'Search models…')} />
                        <CommandList>
                            <CommandEmpty>{t('ai.connections.noModels', 'No matching models. Enter a model ID manually.')}</CommandEmpty>
                            {[...new Set(models)].map((model) => (
                                <CommandItem key={model} value={model} onSelect={() => { onChange(model); setOpen(false); }}>
                                    <span className="sk-model-picker__name">{model}</span>
                                    {value === model && <Check size={16} />}
                                </CommandItem>
                            ))}
                        </CommandList>
                    </Command>
                </PopoverContent>
            </Popover>
        </div>
    );
}
