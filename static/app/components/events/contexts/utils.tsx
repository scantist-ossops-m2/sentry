import {Fragment} from 'react';
import styled from '@emotion/styled';
import startCase from 'lodash/startCase';
import moment from 'moment-timezone';

import StructuredEventData from 'sentry/components/structuredEventData';
import {t} from 'sentry/locale';
import plugins from 'sentry/plugins';
import {space} from 'sentry/styles/space';
import type {Event, KeyValueListData} from 'sentry/types';
import {defined, toTitleCase} from 'sentry/utils';

import {AppEventContext} from './app';
import {BrowserEventContext} from './browser';
import {DefaultContext} from './default';
import {DeviceEventContext} from './device';
import {GPUEventContext} from './gpu';
import {MemoryInfoEventContext} from './memoryInfo';
import {OperatingSystemEventContext} from './operatingSystem';
import {ProfileEventContext} from './profile';
import {ReduxContext} from './redux';
import {RuntimeEventContext} from './runtime';
import {StateEventContext} from './state';
import {ThreadPoolInfoEventContext} from './threadPoolInfo';
import {TraceEventContext} from './trace';
import {UnityEventContext} from './unity';
import {UserEventContext} from './user';

const CONTEXT_TYPES = {
  default: DefaultContext,
  app: AppEventContext,
  device: DeviceEventContext,
  memory_info: MemoryInfoEventContext,
  browser: BrowserEventContext,
  os: OperatingSystemEventContext,
  unity: UnityEventContext,
  runtime: RuntimeEventContext,
  user: UserEventContext,
  gpu: GPUEventContext,
  trace: TraceEventContext,
  threadpool_info: ThreadPoolInfoEventContext,
  state: StateEventContext,
  profile: ProfileEventContext,

  // 'redux.state' will be replaced with more generic context called 'state'
  'redux.state': ReduxContext,
  // 'ThreadPool Info' will be replaced with 'threadpool_info' but
  // we want to keep it here for now so it works for existing versions
  'ThreadPool Info': ThreadPoolInfoEventContext,
  // 'Memory Info' will be replaced with 'memory_info' but
  // we want to keep it here for now so it works for existing versions
  'Memory Info': MemoryInfoEventContext,
};

export function getContextComponent(type: string) {
  return CONTEXT_TYPES[type] || plugins.contexts[type] || CONTEXT_TYPES.default;
}

export function getSourcePlugin(pluginContexts: Array<any>, contextType: string) {
  if (CONTEXT_TYPES[contextType]) {
    return null;
  }
  for (const plugin of pluginContexts) {
    if (plugin.contexts.indexOf(contextType) >= 0) {
      return plugin;
    }
  }
  return null;
}

export function getRelativeTimeFromEventDateCreated(
  eventDateCreated: string,
  timestamp?: string,
  showTimestamp = true
) {
  if (!defined(timestamp)) {
    return timestamp;
  }

  const dateTime = moment(timestamp);

  if (!dateTime.isValid()) {
    return timestamp;
  }

  const relativeTime = `(${dateTime.from(eventDateCreated, true)} ${t(
    'before this event'
  )})`;

  if (!showTimestamp) {
    return <RelativeTime>{relativeTime}</RelativeTime>;
  }

  return (
    <Fragment>
      {timestamp}
      <RelativeTime>{relativeTime}</RelativeTime>
    </Fragment>
  );
}

export function getKnownData<Data, DataType>({
  data,
  knownDataTypes,
  meta,
  raw,
  onGetKnownDataDetails,
}: {
  data: Data;
  knownDataTypes: string[];
  onGetKnownDataDetails: (props: {data: Data; type: DataType}) =>
    | {
        subject: string;
        value?: React.ReactNode;
      }
    | undefined;
  meta?: Record<any, any>;
  raw?: boolean;
}): KeyValueListData {
  const filteredTypes = knownDataTypes.filter(knownDataType => {
    if (
      typeof data[knownDataType] !== 'number' &&
      typeof data[knownDataType] !== 'boolean' &&
      !data[knownDataType]
    ) {
      return !!meta?.[knownDataType];
    }
    return true;
  });

  return filteredTypes
    .map(type => {
      const knownDataDetails = onGetKnownDataDetails({
        data,
        type: type as unknown as DataType,
      });

      if (!knownDataDetails) {
        return null;
      }

      return {
        key: type,
        ...knownDataDetails,
        value: raw ? (
          knownDataDetails.value
        ) : (
          <StructuredEventData
            data={knownDataDetails.value}
            meta={meta?.[type]}
            withAnnotatedText
          />
        ),
      };
    })
    .filter(defined);
}

export function getUnknownData({
  allData,
  knownKeys,
  meta,
}: {
  allData: Record<string, any>;
  knownKeys: string[];
  meta?: NonNullable<Event['_meta']>[keyof Event['_meta']];
}): KeyValueListData {
  return Object.entries(allData)
    .filter(
      ([key]) =>
        key !== 'type' &&
        key !== 'title' &&
        !knownKeys.includes(key) &&
        (typeof allData[key] !== 'number' && !allData[key] ? !!meta?.[key]?.[''] : true)
    )
    .map(([key, value]) => ({
      key,
      value,
      subject: startCase(key),
      meta: meta?.[key]?.[''],
    }));
}

export function getContextTitle({
  alias,
  type,
  value = {},
}: {
  alias: string;
  type: string;
  value?: Record<string, any>;
}) {
  if (defined(value.title) && typeof value.title !== 'object') {
    return value.title;
  }

  if (!defined(type)) {
    return toTitleCase(alias);
  }

  switch (type) {
    case 'app':
      return t('App');
    case 'device':
      return t('Device');
    case 'os':
      return t('Operating System');
    case 'user':
      return t('User');
    case 'gpu':
      return t('Graphics Processing Unit');
    case 'runtime':
      return t('Runtime');
    case 'trace':
      return t('Trace Details');
    case 'otel':
      return t('OpenTelemetry');
    case 'unity':
      return t('Unity');
    case 'memory_info': // Current value for memory info
    case 'Memory Info': // Legacy for memory info
      return t('Memory Info');
    case 'threadpool_info': // Current value for thread pool info
    case 'ThreadPool Info': // Legacy value for thread pool info
      return t('Thread Pool Info');
    case 'default':
      if (alias === 'state') {
        return t('Application State');
      }
      return toTitleCase(alias);
    default:
      return toTitleCase(type);
  }
}

export function getContextMeta(event: Event, type: string): Record<string, any> {
  const defaultMeta = event._meta?.contexts?.[type] ?? {};
  switch (type) {
    case 'memory_info': // Current
    case 'Memory Info': // Legacy
      return event._meta?.contexts?.['Memory Info'] ?? defaultMeta;
    case 'threadpool_info': // Current
    case 'ThreadPool Info': // Legacy
      return event._meta?.contexts?.['ThreadPool Info'] ?? defaultMeta;
    case 'user':
      return event._meta?.user ?? defaultMeta;
    default:
      return defaultMeta;
  }
}

const RelativeTime = styled('span')`
  color: ${p => p.theme.subText};
  margin-left: ${space(0.5)};
`;
