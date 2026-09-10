# The first interrupted production rollout left these superseded resources in
# state. Keep the cloud objects intact while moving the active stack to direct
# encrypted function configuration and the existing invoker account. This is a
# one-release state migration; remove these blocks after it has been applied.
removed {
  from = yandex_iam_service_account.gateway

  lifecycle {
    destroy = false
  }
}

removed {
  from = yandex_lockbox_secret.runtime

  lifecycle {
    destroy = false
  }
}

removed {
  from = yandex_lockbox_secret_version.runtime

  lifecycle {
    destroy = false
  }
}
