import {Fragment} from 'react';

import ContextBlock from 'sentry/components/events/contexts/contextBlock';
import type {Event} from 'sentry/types/event';

import {getContextMeta, getKnownData, getUnknownData} from '../utils';

import {getGPUKnownDataDetails} from './getGPUKnownDataDetails';
import type {GPUData} from './types';
import {GPUKnownDataType} from './types';

type Props = {
  data: GPUData;
  event: Event;
  meta?: Record<string, any>;
};

export const gpuKnownDataValues = [
  GPUKnownDataType.NAME,
  GPUKnownDataType.VERSION,
  GPUKnownDataType.VENDOR_NAME,
  GPUKnownDataType.MEMORY_SIZE,
  GPUKnownDataType.NPOT_SUPPORT,
  GPUKnownDataType.MULTI_THREAD_RENDERING,
  GPUKnownDataType.API_TYPE,
];

const gpuIgnoredDataValues = [];

export function GPUEventContext({data, event, meta: propsMeta}: Props) {
  const gpuValues = [...gpuKnownDataValues];
  const meta = propsMeta ?? getContextMeta(event, 'gpu');

  if (data.vendor_id > 0) {
    gpuValues.unshift(GPUKnownDataType.VENDOR_ID);
  }

  if (data.id > 0) {
    gpuValues.unshift(GPUKnownDataType.ID);
  }

  return (
    <Fragment>
      <ContextBlock
        data={getKnownData<GPUData, GPUKnownDataType>({
          data,
          meta,
          knownDataTypes: gpuValues,
          onGetKnownDataDetails: v => getGPUKnownDataDetails(v),
        })}
      />
      <ContextBlock
        data={getUnknownData({
          allData: data,
          knownKeys: [...gpuValues, ...gpuIgnoredDataValues],
          meta,
        })}
      />
    </Fragment>
  );
}
