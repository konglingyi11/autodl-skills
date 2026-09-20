# HTTP contract and evidence

The added interfaces were inspected through unauthenticated HTTP downloads of the official AutoDL frontend on **2026-09-20**. This establishes client endpoint/method/payload provenance, not authenticated server success. The process environment had neither `AUTODL_SUB_ACCOUNT` nor `AUTODL_SUB_PASSWORD`; no authenticated additions, paid creation, clone, image save, or payment were submitted. Synthetic offline tests cover request construction and failure boundaries. The public `image-billing` command was exercised against the official pricing document.

## Sources

All assets are under `https://www.autodl.com/assets/`:

| Asset | Evidence |
| --- | --- |
| `components.36e5184c.js` | HTTP interceptor; sub-account route rewriting, Authorization and AppVersion headers; login/ticket exchange |
| `instance.4daa8be5.js` | Instance list, power, snapshot, clone payg/prepay endpoints and methods |
| `service-code.30826265.js` | Host search defaults, regions, base images, create endpoints, order read/payment |
| `myImage.682e0f65.js` | Image list, eligible private-image catalog, usage, name check, save endpoints |
| `index.39ade2c9.js` | Image-list filters, field names, status values, storage units, billing display |
| `index.6f23b99d.js` | Save payload; clone source-to-form fields; disk capacity from snapshot |
| `index.e8b814c8.js` | Complete create/clone request shape, GPU/host availability, price conversion |
| `getPrice.b8d5d4ee.js` | POST price preview |
| `utils.a30776c6.js` | CPU architecture and chip filters for eligible images |
| `system-image.vue_vue&type=style&index=0&lang.d3e67626.js` | Eligible private image query and image metadata |
| `index.acd728e3.js` | Order response nesting, `deal_price`, and `PUT order/pay` submission |

Current documentation: [pricing](https://www.autodl.com/docs/price/) and [image disk scope](https://www.autodl.com/docs/image/). Do not embed credentials in evidence files. Public client assets can change; refresh links from the site's HTML and import graph with HTTP if a pinned asset disappears. Do not guess many URLs or mutation payloads.

## Routing and authentication

The official interceptor inserts `sub_user` after `api/v1` for an authenticated sub-account on the ordinary-container/image/order endpoints below. The helper sends `/api/v1/sub_user/<resource>`; do not fall back to main-account endpoints on a permission denial. Pro/file-storage exceptions in the interceptor are outside this helper.

Login retains the existing helper's flow: public `GET /version.json` supplies `AppVersion`, `POST /api/v1/sub_user/login` carries `sub_name` and the SHA-1 password digest, and `POST /api/v1/sub_user/passport` exchanges the ticket and device metadata for a token. Credentials come from environment variables and the token remains only in memory. No browser token or cookie is read. Non-success API codes are errors, not an empty list; raw server messages and full responses are not printed.

## Resource endpoints

Paths below are relative to `/api/v1/sub_user/`:

| Method | Resource | Request/response contract |
| --- | --- | --- |
| POST | `instance` | `page_index`, `page_size`, `status`, `charge_type`, `sub_name`, `unbind_sub_user`; `data.list`, `data.max_page` |
| POST | `instance/power_on`, `instance/power_off` | `instance_uuid` |
| GET | `instance/snapshot` | Query `instance_uuid`; `data.image`, `data.machine_info_snapshot` |
| POST | `image/private/list` | `order_uuid`, `date_from`, `date_to`, `page_index`, `page_size`; paginated image records |
| POST | `image/private/get` | `cpu_arch` and `chip_corp` arrays; eligible private images in `data` array |
| POST | `image/private/usage` | No request body; `current_usage`, `max_usage`, `free_size` in bytes, `to_cost` already in yuan |
| POST | `image/private/check_name` | `image_id`, `image_name`; `data.existed` |
| POST | `instance/image/save` | `image_name`, `instance_uuid`, `try_again: false` |
| GET | `region/list` | Region catalog; retained name/sign fields only |
| POST | `user/machine/list` | Frontend search defaults, optional `machine_id`, `region_sign_list`, `gpu_type_name`; paginated `list` and `result_total` |
| POST | `image/get` | `image_uuid`; base-image metadata in `data` array |
| POST | `order/price/preview` | `machine_id`, `charge_type`, `duration`, `num`, `expand_data_disk` bytes, `coupon_id_list` |
| POST | `order/instance/create/payg`, `order/instance/create/prepay` | `instance_info` plus matching `price_info` |
| POST | `order/instance/clone/payg`, `order/instance/clone/prepay` | Same nested objects plus source `instance_uuid` |
| GET | `order` | Query `order_uuid`; actual order is nested in `data.order` |
| PUT | `order/pay` | `order_uuid`; a separate, explicitly executed create/clone-order payment |

Create `instance_info` uses `machine_id`, `charge_type`, `req_gpu_amount`, `instance_name`, `expand_data_disk`, the selected `base_image_uuid` or `private_image_uuid`, and the frontend's empty reproduction/application fields. Clone derives these image fields from the source instance and snapshot, adding `copy_data_disk_after_clone`, `keep_src_user_service_address_after_clone: false`, and `ft_sparse`.

Clone source base-image identification is `snapshot.image.image_name` only when `image_type == "public"`, matching the frontend. The source's base data allocation is `snapshot.machine_info_snapshot.max_instance_disk_size`; expanded capacity is `source.expand_data_disk_size`. Do not substitute an invented `instance_disk_size` field.

Image states in the UI are `saving`, `failed`, and `finished`. A filtered private-image catalog alone does not establish completion; the helper also checks the image-list status. Image sizes are bytes. `storage_oss_sign` describes cached regions; absence does not establish instantaneous availability everywhere.

The frontend divides pricing API amounts by 1000 to show yuan; image `usage.to_cost` is already yuan. Its `discount_list` iteration replaces each product's displayed final value using `(origin_price - total_reduction) / 1000`. The helper preserves the safe source fields as well as this display conversion. Recurring disk pricing comes separately from `data_disk_info.data_disk_deal_daily_price`. Missing price fields remain unknown. There is no client-side guarantee against a price change after the quote.

## Validation boundaries

- Paginated output requires recognized pagination metadata; schema drift fails closed instead of silently truncating results.
- Read-only preview requests do not submit orders/images. Execution binds request, source/image, host configuration, and price to a SHA-256 fingerprint and rechecks current state. This is a local change detector, not cryptographic proof of authorization or server-side quote locking.
- Receipts use exclusive file creation before a single mutation request. A timeout leaves `SUBMITTING` unresolved. Reusing the same receipt is blocked; choosing another filename does not safely resolve an earlier uncertain request.
- Live sub-account permission, catalog/region response details, billing execution, image caching, clone quota and provisioning completion remain unverified in this revision. Failures should lead to targeted HTTP inspection, not browser fallback, account switching, or blind retries.
- Full filesystem-copy integrity, source retention deadlines, and experiment resumption require their own verification. No test should be mistaken for evidence that a live migration completed.
