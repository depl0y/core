"""The Tile component."""
from datetime import timedelta
from functools import partial

from pytile import async_login
from pytile.errors import InvalidAuthError, SessionExpiredError, TileError

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client, entity_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.async_ import gather_with_concurrency

from .const import DATA_COORDINATOR, DATA_TILE, DOMAIN, LOGGER

PLATFORMS = ["device_tracker"]
DEVICE_TYPES = ["PHONE", "TILE"]

DEFAULT_INIT_TASK_LIMIT = 2
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=2)

CONF_SHOW_INACTIVE = "show_inactive"


async def async_setup(hass, config):
    """Set up the Tile component."""
    hass.data[DOMAIN] = {DATA_COORDINATOR: {}, DATA_TILE: {}}
    return True


async def async_setup_entry(hass, entry):
    """Set up Tile as config entry."""
    hass.data[DOMAIN][DATA_COORDINATOR][entry.entry_id] = {}
    hass.data[DOMAIN][DATA_TILE][entry.entry_id] = {}

    # The existence of shared Tiles across multiple accounts requires an entity ID
    # change:
    #
    # Old: tile_{uuid}
    # New: {username}_{uuid}
    #
    # Find any entities with the old format and update them:
    ent_reg = entity_registry.async_get(hass)
    for entity in [
        e
        for e in ent_reg.entities.values()
        if e.config_entry_id == entry.entry_id
        and not e.unique_id.startswith(entry.data[CONF_USERNAME])
    ]:
        new_unique_id = f"{entry.data[CONF_USERNAME]}_".join(
            entity.unique_id.split(f"{DOMAIN}_")
        )
        LOGGER.debug(
            "Migrating entity %s from old unique ID '%s' to new unique ID '%s'",
            entity.entity_id,
            entity.unique_id,
            new_unique_id,
        )
        ent_reg.async_update_entity(entity.entity_id, new_unique_id=new_unique_id)

    websession = aiohttp_client.async_get_clientsession(hass)

    try:
        client = await async_login(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            session=websession,
        )
        hass.data[DOMAIN][DATA_TILE][entry.entry_id] = await client.async_get_tiles()
    except InvalidAuthError:
        LOGGER.error("Invalid credentials provided")
        return False
    except TileError as err:
        raise ConfigEntryNotReady("Error during integration setup") from err

    async def async_update_tile(tile):
        """Update the Tile."""
        try:
            return await tile.async_update()
        except SessionExpiredError:
            LOGGER.info("Tile session expired; creating a new one")
            await client.async_init()
        except TileError as err:
            raise UpdateFailed(f"Error while retrieving data: {err}") from err

    coordinator_init_tasks = []
    for tile_uuid, tile in hass.data[DOMAIN][DATA_TILE][entry.entry_id].items():
        coordinator = hass.data[DOMAIN][DATA_COORDINATOR][entry.entry_id][
            tile_uuid
        ] = DataUpdateCoordinator(
            hass,
            LOGGER,
            name=tile.name,
            update_interval=DEFAULT_UPDATE_INTERVAL,
            update_method=partial(async_update_tile, tile),
        )
        coordinator_init_tasks.append(coordinator.async_refresh())

    await gather_with_concurrency(DEFAULT_INIT_TASK_LIMIT, *coordinator_init_tasks)

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    return True


async def async_unload_entry(hass, entry):
    """Unload a Tile config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN][DATA_COORDINATOR].pop(entry.entry_id)
    return unload_ok
