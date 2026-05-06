# Iteration 1: Public Domain Access and Health Check

## Goal

Make the service running on the home desktop reachable from the public internet
through the user's Tencent Cloud domain.

## External Acceptance Feature

A requester outside the home network can open:

```text
https://<domain>/health
```

and receive a successful health response from the service running on the home
desktop.

## Scope

- Configure Tencent Cloud DNS so the domain has an A record pointing to the home
  broadband public IPv4 address.
- Configure the home desktop with a stable LAN IP address.
- Configure router port forwarding from the public internet to the desktop.
- Configure Windows Firewall to allow the selected service port.
- Configure HTTPS for the public domain.
- Define a DDNS updater that detects home broadband IP changes and updates the
  Tencent Cloud DNS A record through Tencent Cloud DNS API.
- Document operational diagnostics for DNS, public IP, port forwarding, firewall,
  HTTPS, and DDNS failures.

## Out of Scope

- Implementing `POST /api/briefings`.
- Creating GitHub Issues.
- Running Codex CLI or PPT generation.
- Buying or configuring a Tencent Cloud CVM server.
- Designing a production multi-region deployment.

## Key Decisions

- The service runs on the home desktop rather than a cloud server.
- The domain points directly to the home broadband public IP.
- DDNS is required because the home broadband IP may change.
- If the broadband connection has no inbound-reachable public IPv4 address, this
  iteration cannot pass as designed.
- HTTPS is part of the external acceptance feature, not a later polish step.

## Implementation Notes

- The health endpoint should return enough information for humans to confirm
  they are reaching the intended service instance: service name, version or build
  identifier, current server time, and status.
- The DDNS updater should compare the current external IP with the existing DNS
  A record before updating, to avoid unnecessary DNS writes.
- The DDNS updater should log each check and each DNS mutation result.
- The runbook should include how to find the current public IP, how to inspect
  Tencent Cloud DNS records, and how to test the public port from outside the
  home network.

## E2E Acceptance Test

### Preconditions

- The service is running on the home desktop.
- The router forwards the selected external port to the desktop.
- Windows Firewall allows inbound traffic for the service.
- Tencent Cloud DNS contains an A record for `<domain>`.
- HTTPS is configured for `<domain>`.
- The tester has a phone with mobile data that is not connected to the home Wi-Fi.

### Steps

1. On the phone, disable Wi-Fi and use mobile data.
2. Open `https://<domain>/health`.
3. Confirm the response is `200 OK`.
4. Confirm the response identifies the briefing generation service.
5. In Tencent Cloud DNS, temporarily change the A record to a wrong IP.
6. Run or wait for the DDNS updater.
7. Confirm the A record is restored to the current home broadband public IP.
8. Open `https://<domain>/health` again from mobile data.

### Expected Result

- The health endpoint is reachable from outside the home network.
- The response proves traffic is reaching the desktop service.
- DDNS repairs an incorrect or stale DNS A record.
- After DNS propagation, the health endpoint becomes reachable again.

## Risks & Diagnostics

- **No public IPv4 or CGNAT:** compare router WAN IP with a public IP lookup
  result. If they differ in a CGNAT pattern, direct inbound access will not work.
- **ISP blocks inbound ports:** test alternate ports and record which ports are
  reachable.
- **Router forwarding is wrong:** verify the desktop LAN IP and target service
  port.
- **Windows Firewall blocks traffic:** test locally first, then from another LAN
  device, then from mobile data.
- **DNS has not propagated:** compare Tencent Cloud DNS record value with
  public DNS lookup results.
- **HTTPS certificate fails:** record whether the failure is issuance, renewal,
  hostname mismatch, or local reverse proxy configuration.

## Done Criteria

- `https://<domain>/health` works from mobile data.
- DDNS behavior is documented and can repair a stale A record.
- Failure diagnostics are written clearly enough for a human operator to follow.
- No API, worker, Codex, or archive behavior is implemented in this iteration.

