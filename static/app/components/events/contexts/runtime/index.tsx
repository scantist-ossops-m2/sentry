import {Fragment} from 'react';

import ContextBlock from 'sentry/components/events/contexts/contextBlock';
import type {Event} from 'sentry/types/event';

import {getContextMeta, getKnownData, getUnknownData} from '../utils';

import {getRuntimeKnownDataDetails} from './getRuntimeKnownDataDetails';
import type {RuntimeData} from './types';
import {RuntimeIgnoredDataType, RuntimeKnownDataType} from './types';

type Props = {
  data: RuntimeData;
  event: Event;
  meta?: Record<string, any>;
};

export const runtimeKnownDataValues = [
  RuntimeKnownDataType.NAME,
  RuntimeKnownDataType.VERSION,
];

const runtimeIgnoredDataValues = [RuntimeIgnoredDataType.BUILD];

export function RuntimeEventContext({data, event, meta: propsMeta}: Props) {
  const meta = propsMeta ?? getContextMeta(event, 'runtime');
  return (
    <Fragment>
      <ContextBlock
        data={getKnownData<RuntimeData, RuntimeKnownDataType>({
          data,
          meta,
          knownDataTypes: runtimeKnownDataValues,
          onGetKnownDataDetails: v => getRuntimeKnownDataDetails(v),
        })}
      />
      <ContextBlock
        data={getUnknownData({
          allData: data,
          knownKeys: [...runtimeKnownDataValues, ...runtimeIgnoredDataValues],
          meta,
        })}
      />
    </Fragment>
  );
}
