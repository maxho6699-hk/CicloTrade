export function createSerialMutationQueue() {
  let tail: Promise<unknown> = Promise.resolve()
  return function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = tail.then(operation, operation)
    tail = result.then(() => undefined, () => undefined)
    return result
  }
}

export function createStateRevisionGuard() {
  let revision = 0
  return {
    snapshot: () => revision,
    advance: () => { revision += 1; return revision },
    isCurrent: (candidate: number) => candidate === revision,
  }
}
