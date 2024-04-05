import styled from '@emotion/styled';

import {getContextMeta, getContextTitle} from 'sentry/components/events/contexts/utils';
import {AnnotatedTextErrors} from 'sentry/components/events/meta/annotatedText/annotatedTextErrors';
import Panel from 'sentry/components/panels/panel';
import {StructuredData} from 'sentry/components/structuredEventData';
import {space} from 'sentry/styles/space';
import type {Group} from 'sentry/types';
import type {Event} from 'sentry/types/event';
import {objectIsEmpty} from 'sentry/utils';

interface ContextCardProps {
  alias: string;
  event: Event;
  type: string;
  group?: Group;
  value?: Record<string, any>;
}

function ContextCard({alias, event, type, value = {}}: ContextCardProps) {
  if (objectIsEmpty(value)) {
    return null;
  }
  const meta = getContextMeta(event, type);

  const content = Object.entries(value).map(([contextKey, contextValue], i) => {
    if (contextKey === 'type') {
      return null;
    }
    const contextErrors = meta?.[contextKey]?.['']?.err ?? [];
    const hasErrors = contextErrors.length > 0;
    return (
      <ContextContent key={i} hasErrors={hasErrors}>
        <ContextKey>{contextKey}</ContextKey>
        <ContextValue hasErrors={hasErrors}>
          <StructuredData
            value={contextValue}
            withAnnotatedText={false}
            depth={0}
            maxDefaultDepth={0}
            meta={meta}
            config={{}}
          />
          <AnnotatedTextErrors errors={contextErrors} />
        </ContextValue>
      </ContextContent>
    );
  });

  return (
    <Card>
      <ContextTitle>{getContextTitle({alias, type, value})}</ContextTitle>
      {content}
    </Card>
  );
}

const Card = styled(Panel)`
  padding: ${space(0.75)};
  display: grid;
  column-gap: ${space(1.5)};
  grid-template-columns: minmax(100px, auto) 1fr;
  font-size: ${p => p.theme.fontSizeSmall};
`;

const ContextTitle = styled('p')`
  grid-column: span 2;
  padding: ${space(0.25)} ${space(0.75)};
  margin: 0;
  color: ${p => p.theme.headingColor};
  font-weight: bold;
`;

const ContextContent = styled('div')<{hasErrors: boolean}>`
  display: grid;
  grid-template-columns: subgrid;
  grid-column: span 2;
  padding: ${space(0.25)} ${space(0.75)};
  border-radius: 4px;
  color: ${p => (p.hasErrors ? p.theme.alert.error.color : p.theme.subText)};
  border: 1px solid ${p => (p.hasErrors ? p.theme.alert.error.border : 'transparent')};
  background-color: ${p =>
    p.hasErrors ? p.theme.alert.error.backgroundLight : p.theme.background};
  &:nth-child(odd) {
    background-color: ${p =>
      p.hasErrors ? p.theme.alert.error.backgroundLight : p.theme.backgroundSecondary};
  }
`;

const ContextKey = styled('div')`
  grid-column: 1 / 2;
  font-family: ${p => p.theme.text.familyMono};
`;

const ContextValue = styled('div')<{hasErrors: boolean}>`
  grid-column: 2 / 3;
  color: ${p => (p.hasErrors ? 'inherit' : p.theme.text)};
  font-family: ${p => p.theme.text.familyMono};
  display: flex;
  justify-content: space-between;
`;

export default ContextCard;
