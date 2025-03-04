import {Fragment} from 'react';

import ContextBlock from 'sentry/components/events/contexts/contextBlock';
import type {Event} from 'sentry/types';

import {getContextMeta, getKnownData, getUnknownData} from '../utils';

import {getBrowserKnownDataDetails} from './getBrowserKnownDataDetails';
import type {BrowserKnownData} from './types';
import {BrowserKnownDataType} from './types';

type Props = {
  data: BrowserKnownData;
  event: Event;
  meta?: Record<string, any>;
};

export const browserKnownDataValues = [
  BrowserKnownDataType.NAME,
  BrowserKnownDataType.VERSION,
];

export function BrowserEventContext({data, event, meta: propsMeta}: Props) {
  const meta = propsMeta ?? getContextMeta(event, 'browser');
  return (
    <Fragment>
      <ContextBlock
        data={getKnownData<BrowserKnownData, BrowserKnownDataType>({
          data,
          meta,
          knownDataTypes: browserKnownDataValues,
          onGetKnownDataDetails: v => getBrowserKnownDataDetails(v),
        })}
      />
      <ContextBlock
        data={getUnknownData({
          allData: data,
          knownKeys: [...browserKnownDataValues],
          meta,
        })}
      />
    </Fragment>
  );
}
