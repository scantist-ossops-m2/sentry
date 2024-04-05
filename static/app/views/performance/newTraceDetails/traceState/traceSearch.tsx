import {
  isAutogroupedNode,
  isSpanNode,
  isTraceErrorNode,
  isTransactionNode,
} from 'sentry/views/performance/newTraceDetails/guards';
import {traceReducerExhaustiveActionCheck} from 'sentry/views/performance/newTraceDetails/traceState';
import type {
  TraceTree,
  TraceTreeNode,
} from 'sentry/views/performance/newTraceDetails/traceTree';

export type TraceSearchAction =
  | {query: string | undefined; type: 'set query'}
  | {type: 'go to first match'}
  | {type: 'go to last match'}
  | {type: 'go to next match'}
  | {type: 'go to previous match'}
  | {
      resultIndex: number;
      resultIteratorIndex: number;
      type: 'set search iterator index';
    }
  | {type: 'clear'}
  | {type: 'clear search iterator index'}
  | {type: 'clear query'}
  | {
      results: ReadonlyArray<TraceResult>;
      resultsLookup: Map<TraceTreeNode<TraceTree.NodeValue>, number>;
      type: 'set results';
      resultIndex?: number;
      resultIteratorIndex?: number;
    };

export type TraceSearchState = {
  query: string | undefined;
  // Index in the list/tree
  resultIndex: number | null;
  // Index in the results array
  resultIteratorIndex: number | null;
  results: ReadonlyArray<TraceResult> | null;
  resultsLookup: Map<TraceTreeNode<TraceTree.NodeValue>, number>;
  status: [ts: number, 'loading' | 'success' | 'error'] | undefined;
};

function assertBoundedIndex(index: number, length: number) {
  if (index < 0 || index > length - 1) {
    throw new Error('Search index out of bounds');
  }
}

export function traceSearchReducer(
  state: TraceSearchState,
  action: TraceSearchAction
): TraceSearchState {
  switch (action.type) {
    case 'clear query': {
      return {
        query: undefined,
        resultIteratorIndex: null,
        results: null,
        resultIndex: null,
        resultsLookup: new Map(),
        status: undefined,
      };
    }
    case 'go to first match': {
      if (!state.results || state.results.length === 0) {
        return state;
      }
      return {
        ...state,
        resultIteratorIndex: 0,
        resultIndex: state.results[0].index,
      };
    }
    case 'go to last match': {
      if (!state.results || state.results.length === 0) {
        return state;
      }
      return {
        ...state,
        resultIteratorIndex: state.results.length - 1,
        resultIndex: state.results[state.results.length - 1].index,
      };
    }
    case 'go to next match': {
      if (state.resultIteratorIndex === null) {
        if (!state.results || state.results.length === 0) {
          return state;
        }
        return {
          ...state,
          resultIteratorIndex: 0,
          resultIndex: state.results[0].index,
        };
      }
      if (!state.results) return state;

      let next = state.resultIteratorIndex + 1;
      if (next > state.results.length - 1) {
        next = 0;
      }

      assertBoundedIndex(next, state.results.length);
      return {
        ...state,
        resultIteratorIndex: next,
        resultIndex: state.results[next].index,
      };
    }
    case 'go to previous match': {
      if (state.resultIteratorIndex === null) {
        if (!state.results || !state.results.length) {
          return state;
        }
        return {
          ...state,
          resultIteratorIndex: state.results.length - 1,
          resultIndex: state.results[state.results.length - 1].index,
        };
      }
      if (!state.results) return state;

      let previous = state.resultIteratorIndex - 1;
      if (previous < 0) {
        previous = state.results.length - 1;
      }

      assertBoundedIndex(previous, state.results.length);
      return {
        ...state,
        resultIteratorIndex: previous,
        resultIndex: state.results[previous].index,
      };
    }
    case 'set results': {
      return {
        ...state,
        status: [performance.now(), 'success'],
        results: action.results,
        resultIteratorIndex: action.resultIteratorIndex ?? null,
        resultIndex: action.resultIndex ?? null,
        resultsLookup: action.resultsLookup,
      };
    }
    case 'set query': {
      return {
        ...state,
        status: [performance.now(), 'loading'],
        query: action.query,
      };
    }

    case 'set search iterator index': {
      return {
        ...state,
        resultIteratorIndex: action.resultIteratorIndex,
        resultIndex: action.resultIndex,
      };
    }

    case 'clear search iterator index':
      return {
        ...state,
        resultIteratorIndex: null,
        resultIndex: null,
      };

    case 'clear': {
      return {...state, resultIteratorIndex: null, resultIndex: null};
    }

    default: {
      traceReducerExhaustiveActionCheck(action);
      return state;
    }
  }
}

type TraceResult = {
  index: number;
  value: TraceTreeNode<TraceTree.NodeValue>;
};

export function searchInTraceTree(
  tree: TraceTree,
  query: string,
  previousNode: TraceTreeNode<TraceTree.NodeValue> | null,
  cb: (
    results: [
      ReadonlyArray<TraceResult>,
      Map<TraceTreeNode<TraceTree.NodeValue>, number>,
      {resultIndex: number | undefined; resultIteratorIndex: number | undefined} | null,
    ]
  ) => void
): {id: number | null} {
  const raf: {id: number | null} = {id: 0};
  let previousNodeResult: {
    resultIndex: number | undefined;
    resultIteratorIndex: number | undefined;
  } | null = null;
  const results: Array<TraceResult> = [];
  const resultLookup = new Map();

  let i = 0;
  let matchCount = 0;
  const count = tree.list.length;

  function search() {
    const ts = performance.now();

    while (i < count && performance.now() - ts < 12) {
      const node = tree.list[i];

      if (searchInTraceSubset(query, node)) {
        results.push({index: i, value: node});
        resultLookup.set(node, matchCount);

        if (previousNode === node) {
          previousNodeResult = {
            resultIndex: i,
            resultIteratorIndex: matchCount,
          };
        }
        matchCount++;
      }
      i++;
    }

    if (i < count) {
      raf.id = requestAnimationFrame(search);
    }

    if (i === count) {
      cb([results, resultLookup, previousNodeResult]);
      raf.id = null;
    }
  }

  raf.id = requestAnimationFrame(search);
  return raf;
}

function searchInTraceSubset(
  query: string,
  node: TraceTreeNode<TraceTree.NodeValue>
): boolean {
  if (isSpanNode(node)) {
    if (node.value.op?.includes(query)) {
      return true;
    }
    if (node.value.description?.includes(query)) {
      return true;
    }
    if (node.value.span_id && node.value.span_id === query) {
      return true;
    }
  }

  if (isTransactionNode(node)) {
    if (node.value['transaction.op']?.includes(query)) {
      return true;
    }
    if (node.value.transaction?.includes(query)) {
      return true;
    }
    if (node.value.event_id && node.value.event_id === query) {
      return true;
    }
  }

  if (isAutogroupedNode(node)) {
    if (node.value.op?.includes(query)) {
      return true;
    }
    if (node.value.description?.includes(query)) {
      return true;
    }
  }

  if (isTraceErrorNode(node)) {
    if (node.value.level === query) {
      return true;
    }
    if (node.value.title?.includes(query)) {
      return true;
    }
  }

  return false;
}
