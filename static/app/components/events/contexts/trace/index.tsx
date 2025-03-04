import ErrorBoundary from 'sentry/components/errorBoundary';
import KeyValueList from 'sentry/components/events/interfaces/keyValueList';
import type {Event} from 'sentry/types/event';
import useOrganization from 'sentry/utils/useOrganization';

import {getContextMeta, getKnownData, getUnknownData} from '../utils';

import {getTraceKnownDataDetails} from './getTraceKnownDataDetails';
import type {TraceKnownData} from './types';
import {TraceKnownDataType} from './types';

export const traceKnownDataValues = [
  TraceKnownDataType.STATUS,
  TraceKnownDataType.TRACE_ID,
  TraceKnownDataType.SPAN_ID,
  TraceKnownDataType.PARENT_SPAN_ID,
  TraceKnownDataType.TRANSACTION_NAME,
  TraceKnownDataType.OP_NAME,
];

const traceIgnoredDataValues = [];

type Props = {
  data: TraceKnownData & Record<string, any>;
  event: Event;
  meta?: Record<string, any>;
};

export function TraceEventContext({event, data, meta: propsMeta}: Props) {
  const organization = useOrganization();
  const meta = propsMeta ?? getContextMeta(event, 'trace');

  return (
    <ErrorBoundary mini>
      <KeyValueList
        data={getKnownData<TraceKnownData, TraceKnownDataType>({
          data,
          meta,
          knownDataTypes: traceKnownDataValues,
          onGetKnownDataDetails: v =>
            getTraceKnownDataDetails({...v, organization, event}),
        }).map(v => ({
          ...v,
          subjectDataTestId: `trace-context-${v.key.toLowerCase()}-value`,
        }))}
        shouldSort={false}
        raw={false}
        isContextData
      />

      <KeyValueList
        data={getUnknownData({
          allData: data,
          knownKeys: [...traceKnownDataValues, ...traceIgnoredDataValues],
          meta,
        })}
        shouldSort={false}
        raw={false}
        isContextData
      />
    </ErrorBoundary>
  );
}
