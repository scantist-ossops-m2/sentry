import {t} from 'sentry/locale';
import type {AggregationOutputType} from 'sentry/utils/discover/fields';
import {DiscoverDatasets} from 'sentry/utils/discover/types';
import type {FieldDefinition} from 'sentry/utils/fields';
import {FieldKind, FieldValueType} from 'sentry/utils/fields';

export enum StarfishType {
  BACKEND = 'backend',
  MOBILE = 'mobile',
  FRONTEND = 'frontend',
}

export enum ModuleName {
  HTTP = 'http',
  DB = 'db',
  RESOURCE = 'resource',
  ALL = '',
  OTHER = 'other',
}

export enum SpanMetricsField {
  SPAN_OP = 'span.op',
  SPAN_DESCRIPTION = 'span.description',
  SPAN_MODULE = 'span.module',
  SPAN_ACTION = 'span.action',
  SPAN_DOMAIN = 'span.domain',
  SPAN_GROUP = 'span.group',
  SPAN_DURATION = 'span.duration',
  SPAN_SELF_TIME = 'span.self_time',
  PROJECT_ID = 'project.id',
  TRANSACTION = 'transaction',
  RESOURCE_RENDER_BLOCKING_STATUS = 'resource.render_blocking_status',
  HTTP_RESPONSE_CONTENT_LENGTH = 'http.response_content_length',
  HTTP_DECODED_RESPONSE_CONTENT_LENGTH = 'http.decoded_response_content_length',
  HTTP_RESPONSE_TRANSFER_SIZE = 'http.response_transfer_size',
  FILE_EXTENSION = 'file_extension',
  OS_NAME = 'os.name',
  APP_START_TYPE = 'app_start_type',
  DEVICE_CLASS = 'device.class',
  CACHE_HIT = 'cache.hit',
}

export type SpanNumberFields =
  | SpanMetricsField.SPAN_SELF_TIME
  | SpanMetricsField.SPAN_DURATION
  | SpanMetricsField.HTTP_DECODED_RESPONSE_CONTENT_LENGTH
  | SpanMetricsField.HTTP_RESPONSE_CONTENT_LENGTH
  | SpanMetricsField.HTTP_RESPONSE_TRANSFER_SIZE;

export type SpanStringFields =
  | 'span.op'
  | 'span.description'
  | 'span.module'
  | 'span.action'
  | 'span.domain'
  | 'span.group'
  | 'transaction'
  | 'transaction.method'
  | 'release'
  | 'os.name'
  | 'span.status_code';

export type SpanMetricsQueryFilters = {
  [Field in SpanStringFields]?: string;
} & {
  [SpanMetricsField.PROJECT_ID]?: string;
};

export type SpanStringArrayFields = 'span.domain';

export const COUNTER_AGGREGATES = ['sum', 'avg', 'min', 'max', 'p100'] as const;
export const DISTRIBUTION_AGGREGATES = ['p50', 'p75', 'p95', 'p99'] as const;

export const AGGREGATES = [...COUNTER_AGGREGATES, ...DISTRIBUTION_AGGREGATES] as const;

export type Aggregate = (typeof AGGREGATES)[number];

export const SPAN_FUNCTIONS = [
  'sps',
  'spm',
  'count',
  'time_spent_percentage',
  'http_response_rate',
  'http_error_count',
] as const;

export type SpanFunctions = (typeof SPAN_FUNCTIONS)[number];

export type MetricsResponse = {
  [Property in SpanNumberFields as `${Aggregate}(${Property})`]: number;
} & {
  [Property in SpanFunctions as `${Property}()`]: number;
} & {
  [Property in SpanStringFields as `${Property}`]: string;
} & {
  [Property in SpanStringArrayFields as `${Property}`]: string[];
} & {
  // TODO: This should include all valid HTTP codes or just all integers
  'http_response_rate(2)': number;
  'http_response_rate(3)': number;
  'http_response_rate(4)': number;
  'http_response_rate(5)': number;
} & {
  ['project.id']: number;
};

export type MetricsFilters = {
  [Property in SpanStringFields as `${Property}`]?: string | string[];
};

export type MetricsProperty = keyof MetricsResponse;

export enum SpanIndexedField {
  RESOURCE_RENDER_BLOCKING_STATUS = 'resource.render_blocking_status',
  HTTP_RESPONSE_CONTENT_LENGTH = 'http.response_content_length',
  SPAN_DURATION = 'span.duration',
  SPAN_SELF_TIME = 'span.self_time',
  SPAN_GROUP = 'span.group', // Span group computed from the normalized description. Matches the group in the metrics data set
  SPAN_MODULE = 'span.module',
  SPAN_DESCRIPTION = 'span.description',
  SPAN_OP = 'span.op',
  ID = 'span_id',
  SPAN_ACTION = 'span.action',
  TRACE = 'trace',
  TRANSACTION_ID = 'transaction.id',
  TRANSACTION_METHOD = 'transaction.method',
  TRANSACTION_OP = 'transaction.op',
  SPAN_DOMAIN = 'span.domain',
  TIMESTAMP = 'timestamp',
  RAW_DOMAIN = 'raw_domain',
  PROJECT = 'project',
  PROJECT_ID = 'project_id',
  PROFILE_ID = 'profile_id',
  TRANSACTION = 'transaction',
  ORIGIN_TRANSACTION = 'origin.transaction',
  REPLAY_ID = 'replay.id',
  BROWSER_NAME = 'browser.name',
  USER = 'user',
  INP = 'measurements.inp',
  INP_SCORE = 'measurements.score.inp',
  INP_SCORE_WEIGHT = 'measurements.score.weight.inp',
  TOTAL_SCORE = 'measurements.score.total',
  RESPONSE_CODE = 'span.status_code',
  CACHE_HIT = 'cache.hit',
}

export type IndexedResponse = {
  [SpanIndexedField.SPAN_DURATION]: number;
  [SpanIndexedField.SPAN_SELF_TIME]: number;
  [SpanIndexedField.SPAN_GROUP]: string;
  [SpanIndexedField.SPAN_MODULE]: string;
  [SpanIndexedField.SPAN_DESCRIPTION]: string;
  [SpanIndexedField.SPAN_OP]: string;
  [SpanIndexedField.ID]: string;
  [SpanIndexedField.SPAN_ACTION]: string;
  [SpanIndexedField.TRACE]: string;
  [SpanIndexedField.TRANSACTION]: string;
  [SpanIndexedField.TRANSACTION_ID]: string;
  [SpanIndexedField.TRANSACTION_METHOD]: string;
  [SpanIndexedField.TRANSACTION_OP]: string;
  [SpanIndexedField.SPAN_DOMAIN]: string[];
  [SpanIndexedField.TIMESTAMP]: string;
  [SpanIndexedField.PROJECT]: string;
  [SpanIndexedField.PROJECT_ID]: number;
  [SpanIndexedField.PROFILE_ID]: string;
  [SpanIndexedField.RESOURCE_RENDER_BLOCKING_STATUS]: '' | 'non-blocking' | 'blocking';
  [SpanIndexedField.HTTP_RESPONSE_CONTENT_LENGTH]: string;
  [SpanIndexedField.ORIGIN_TRANSACTION]: string;
  [SpanIndexedField.REPLAY_ID]: string;
  [SpanIndexedField.BROWSER_NAME]: string;
  [SpanIndexedField.USER]: string;
  [SpanIndexedField.INP]: number;
  [SpanIndexedField.INP_SCORE]: number;
  [SpanIndexedField.INP_SCORE_WEIGHT]: number;
  [SpanIndexedField.TOTAL_SCORE]: number;
  [SpanIndexedField.RESPONSE_CODE]: string;
  [SpanIndexedField.CACHE_HIT]: '' | 'true' | 'false';
};

export type IndexedProperty = keyof IndexedResponse;

// TODO: When convenient, remove this alias and use `IndexedResponse` everywhere
export type SpanIndexedFieldTypes = IndexedResponse;

export type Op = SpanIndexedFieldTypes[SpanIndexedField.SPAN_OP];

export enum SpanFunction {
  SPS = 'sps',
  SPM = 'spm',
  TIME_SPENT_PERCENTAGE = 'time_spent_percentage',
  HTTP_ERROR_COUNT = 'http_error_count',
  HTTP_RESPONSE_RATE = 'http_response_rate',
}

export const StarfishDatasetFields = {
  [DiscoverDatasets.SPANS_METRICS]: SpanIndexedField,
  [DiscoverDatasets.SPANS_INDEXED]: SpanIndexedField,
};

export const STARFISH_AGGREGATION_FIELDS: Record<
  SpanFunction,
  FieldDefinition & {defaultOutputType: AggregationOutputType}
> = {
  [SpanFunction.SPS]: {
    desc: t('Spans per second'),
    kind: FieldKind.FUNCTION,
    defaultOutputType: 'number',
    valueType: FieldValueType.NUMBER,
  },
  [SpanFunction.SPM]: {
    desc: t('Spans per minute'),
    kind: FieldKind.FUNCTION,
    defaultOutputType: 'number',
    valueType: FieldValueType.NUMBER,
  },
  [SpanFunction.TIME_SPENT_PERCENTAGE]: {
    desc: t('Span time spent percentage'),
    defaultOutputType: 'percentage',
    kind: FieldKind.FUNCTION,
    valueType: FieldValueType.NUMBER,
  },
  [SpanFunction.HTTP_ERROR_COUNT]: {
    desc: t('Count of 5XX http errors'),
    defaultOutputType: 'integer',
    kind: FieldKind.FUNCTION,
    valueType: FieldValueType.NUMBER,
  },
  [SpanFunction.HTTP_RESPONSE_RATE]: {
    desc: t('Percentage of HTTP responses by code'),
    defaultOutputType: 'percentage',
    kind: FieldKind.FUNCTION,
    valueType: FieldValueType.NUMBER,
  },
};
